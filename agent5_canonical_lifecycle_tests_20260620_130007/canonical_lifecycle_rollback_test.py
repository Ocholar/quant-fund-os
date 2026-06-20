import json
import math
import sys
import traceback
from datetime import datetime

import main as qfos


RESULTS = []
PREFIX = "QFOSLIFE"

def now_tag():
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")

def symbol(name):
    return f"{PREFIX}_{name}_{now_tag()}/USDT"

def f(v):
    return float(v or 0.0)

def ledger(conn):
    row = conn.execute(qfos.text("""
        SELECT *
        FROM qfos_current_ledger_accounting()
        LIMIT 1
    """)).mappings().first()
    return dict(row or {})

def position(conn, sym):
    row = conn.execute(qfos.text("""
        SELECT symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl
        FROM positions
        WHERE symbol = :symbol
        LIMIT 1
    """), {"symbol": sym}).mappings().first()
    return dict(row or {})

def trade_rows(conn, sym):
    rows = conn.execute(qfos.text("""
        SELECT
            id, symbol, side, quantity, expected_price, fill_price,
            pnl, strategy, is_exit, exit_reason, lifecycle_key
        FROM trades
        WHERE symbol = :symbol
        ORDER BY id
    """), {"symbol": sym}).mappings().all()
    return [dict(x) for x in rows]

def assert_true(name, condition, detail):
    RESULTS.append({
        "test": name,
        "status": "PASS" if condition else "FAIL",
        "detail": detail,
    })
    if not condition:
        raise AssertionError(f"{name}: {detail}")

def fill(sym, side, qty, price, strategy, exit_reason=None):
    payload = {
        "symbol": sym,
        "side": side,
        "quantity": float(qty),
        "qty": float(qty),
        "expected_price": float(price),
        "fill_price": float(price),
        "slippage_bps": 0.0,
        "strategy": strategy,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }
    if side == "sell":
        payload["is_exit"] = True
        payload["exit_reason"] = exit_reason or strategy
        payload["reason"] = exit_reason or strategy
    return payload

def apply(conn, payload, source):
    return qfos.qfos_apply_fill_atomic(conn, payload, source=source)

summary = {
    "started_at_utc": datetime.utcnow().isoformat(),
    "tests": RESULTS,
    "rollback_completed": False,
    "fatal_error": None,
}

conn = qfos.engine.connect()
tx = conn.begin()

try:
    before = ledger(conn)

    # --------------------------------------------------------
    # 1. BUY reduces available cash and creates DB position.
    # --------------------------------------------------------
    sym_buy = symbol("BUY")
    cash_before = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    buy_ok = apply(
        conn,
        fill(sym_buy, "buy", 2.0, 5.0, "canonical_test_buy"),
        "canonical_rollback_test",
    )
    cash_after = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    pos_after_buy = position(conn, sym_buy)

    assert_true(
        "buy_persists_and_reduces_cash",
        bool(buy_ok) and f(pos_after_buy.get("quantity")) > 0 and cash_after < cash_before,
        {
            "buy_ok": bool(buy_ok),
            "cash_before": cash_before,
            "cash_after": cash_after,
            "position": pos_after_buy,
        },
    )

    # --------------------------------------------------------
    # 2. Take-profit SELL raises cash, closes position, is_exit.
    # --------------------------------------------------------
    cash_before_sell = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    tp_ok = apply(
        conn,
        fill(
            sym_buy, "sell", 2.0, 5.2,
            "take_profit_exit",
            "take_profit_exit",
        ),
        "canonical_rollback_test",
    )
    cash_after_sell = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    pos_after_tp = position(conn, sym_buy)
    rows_tp = trade_rows(conn, sym_buy)
    sell_tp = rows_tp[-1] if rows_tp else {}

    assert_true(
        "take_profit_sell_uses_boundary_and_increases_cash",
        (
            bool(tp_ok)
            and cash_after_sell > cash_before_sell
            and f(pos_after_tp.get("quantity")) <= 0.00000001
            and str(sell_tp.get("side", "")).lower() == "sell"
            and bool(sell_tp.get("is_exit"))
            and str(sell_tp.get("exit_reason") or "") == "take_profit_exit"
        ),
        {
            "sell_ok": bool(tp_ok),
            "cash_before": cash_before_sell,
            "cash_after": cash_after_sell,
            "position_after": pos_after_tp,
            "sell_trade": sell_tp,
        },
    )

    # --------------------------------------------------------
    # 3. Stop-loss uses same canonical cost-basis authority.
    # --------------------------------------------------------
    sym_sl = symbol("STOPLOSS")
    buy_sl_ok = apply(
        conn,
        fill(sym_sl, "buy", 3.0, 4.0, "canonical_test_buy"),
        "canonical_rollback_test",
    )
    pos_before_sl = position(conn, sym_sl)

    sl_ok = apply(
        conn,
        fill(
            sym_sl, "sell", 3.0, 3.8,
            "sideways_stop_loss_exit",
            "sideways_stop_loss_exit",
        ),
        "canonical_rollback_test",
    )
    pos_after_sl = position(conn, sym_sl)
    rows_sl = trade_rows(conn, sym_sl)
    sell_sl = rows_sl[-1] if rows_sl else {}

    assert_true(
        "stop_loss_sell_uses_canonical_cost_basis",
        (
            bool(buy_sl_ok)
            and bool(sl_ok)
            and abs(f(pos_before_sl.get("avg_entry")) - 4.0) < 0.0001
            and f(pos_after_sl.get("quantity")) <= 0.00000001
            and str(sell_sl.get("exit_reason") or "") == "sideways_stop_loss_exit"
        ),
        {
            "buy_ok": bool(buy_sl_ok),
            "sell_ok": bool(sl_ok),
            "position_before": pos_before_sl,
            "position_after": pos_after_sl,
            "sell_trade": sell_sl,
        },
    )

    # --------------------------------------------------------
    # 4. Duplicate SELL cannot create a second durable trade row.
    # --------------------------------------------------------
    sym_dup = symbol("DUP")
    apply(
        conn,
        fill(sym_dup, "buy", 1.0, 10.0, "canonical_test_buy"),
        "canonical_rollback_test",
    )

    first_sell = apply(
        conn,
        fill(
            sym_dup, "sell", 1.0, 10.1,
            "take_profit_exit",
            "take_profit_exit",
        ),
        "path_a",
    )
    count_after_first = len([
        r for r in trade_rows(conn, sym_dup)
        if str(r.get("side", "")).lower() == "sell"
    ])

    second_sell = apply(
        conn,
        fill(
            sym_dup, "sell", 1.0, 10.1,
            "watchdog_exit",
            "watchdog_exit",
        ),
        "path_b",
    )
    count_after_second = len([
        r for r in trade_rows(conn, sym_dup)
        if str(r.get("side", "")).lower() == "sell"
    ])

    assert_true(
        "duplicate_sell_is_rejected_or_non_persistent",
        (
            bool(first_sell)
            and not bool(second_sell)
            and count_after_first == 1
            and count_after_second == 1
        ),
        {
            "first_sell": bool(first_sell),
            "second_sell": bool(second_sell),
            "sell_count_after_first": count_after_first,
            "sell_count_after_second": count_after_second,
            "rows": trade_rows(conn, sym_dup),
        },
    )

    # --------------------------------------------------------
    # 5. Oversell cannot exceed current DB position.
    # --------------------------------------------------------
    sym_over = symbol("OVERSELL")
    apply(
        conn,
        fill(sym_over, "buy", 1.0, 6.0, "canonical_test_buy"),
        "canonical_rollback_test",
    )
    over_before = position(conn, sym_over)

    oversell_result = apply(
        conn,
        fill(
            sym_over, "sell", 2.0, 6.1,
            "manual_exit",
            "manual_exit",
        ),
        "canonical_rollback_test",
    )
    over_after = position(conn, sym_over)
    over_sells = [
        r for r in trade_rows(conn, sym_over)
        if str(r.get("side", "")).lower() == "sell"
    ]
    total_sell_qty = sum(f(r.get("quantity")) for r in over_sells)

    assert_true(
        "oversell_is_rejected_or_safely_clamped",
        total_sell_qty <= f(over_before.get("quantity")) + 0.00000001,
        {
            "oversell_result": bool(oversell_result),
            "position_before": over_before,
            "position_after": over_after,
            "total_sell_qty": total_sell_qty,
            "sell_rows": over_sells,
        },
    )

    # --------------------------------------------------------
    # 6. Invalid persistence request mutates neither cash nor position.
    # --------------------------------------------------------
    sym_bad = symbol("BAD")
    cash_before_bad = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    bad_result = apply(
        conn,
        fill(sym_bad, "buy", 1.0, 0.0, "canonical_test_bad_buy"),
        "canonical_rollback_test",
    )
    cash_after_bad = f(ledger(conn).get("cash") or ledger(conn).get("available_cash"))
    bad_pos = position(conn, sym_bad)
    bad_rows = trade_rows(conn, sym_bad)

    assert_true(
        "failed_persistence_does_not_mutate_cash_or_position",
        (
            not bool(bad_result)
            and abs(cash_after_bad - cash_before_bad) < 0.00000001
            and f(bad_pos.get("quantity")) <= 0.00000001
            and len(bad_rows) == 0
        ),
        {
            "result": bool(bad_result),
            "cash_before": cash_before_bad,
            "cash_after": cash_after_bad,
            "position": bad_pos,
            "trades": bad_rows,
        },
    )

    # --------------------------------------------------------
    # 7. Dust-residual SELL must not create negative quantity.
    # --------------------------------------------------------
    sym_dust = symbol("DUST")
    apply(
        conn,
        fill(sym_dust, "buy", 1.0, 2.0, "canonical_test_buy"),
        "canonical_rollback_test",
    )

    dust_result = apply(
        conn,
        fill(
            sym_dust, "sell", 0.999999999, 2.1,
            "dust_test_exit",
            "dust_test_exit",
        ),
        "canonical_rollback_test",
    )
    dust_pos = position(conn, sym_dust)

    assert_true(
        "dust_residual_is_safe",
        f(dust_pos.get("quantity")) >= -0.00000001,
        {
            "result": bool(dust_result),
            "position_after": dust_pos,
            "rows": trade_rows(conn, sym_dust),
        },
    )

    # --------------------------------------------------------
    # 8. Ledger/equity invariant after all test actions.
    # --------------------------------------------------------
    after = ledger(conn)
    cash = f(after.get("cash") or after.get("available_cash"))
    exposure = f(after.get("exposure") or after.get("open_exposure"))
    equity = f(after.get("equity"))

    assert_true(
        "ledger_equity_equals_cash_plus_exposure",
        abs(equity - (cash + exposure)) < 0.0001,
        {
            "cash": cash,
            "exposure": exposure,
            "equity": equity,
            "difference": equity - (cash + exposure),
        },
    )

except Exception as exc:
    summary["fatal_error"] = repr(exc)
    summary["traceback"] = traceback.format_exc()

finally:
    try:
        tx.rollback()
        summary["rollback_completed"] = True
    except Exception as exc:
        summary["rollback_error"] = repr(exc)

    try:
        conn.close()
    except Exception:
        pass

summary["finished_at_utc"] = datetime.utcnow().isoformat()

print(json.dumps(summary, indent=2, default=str))

if summary["fatal_error"]:
    sys.exit(1)

if any(x["status"] != "PASS" for x in RESULTS):
    sys.exit(2)
