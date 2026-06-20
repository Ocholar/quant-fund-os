from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1" in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1
#
# Agent 2 scope:
#   Exit decision policy: TP, SL, sideways stagnation,
#   max hold, trailing profit, breakeven protection.
#
# Agent 5 scope:
#   SELL execution safety: valid open quantity only,
#   no duplicate full exits, is_exit=true, exit_reason populated,
#   quantity capped to open quantity, persistence via atomic boundary.
#
# This block does not alter entry allocation, feature generation,
# live-trading setting, strategy scoring, or cash/equity formulas.
# ============================================================

QFOS_EXIT_TAKE_PROFIT_PCT = 0.0085
QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT = 0.0055
QFOS_EXIT_STOP_LOSS_PCT = -0.0065
QFOS_EXIT_SIDEWAYS_STOP_LOSS_PCT = -0.0045

QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE = 20.0
QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_PNL = -0.0025
QFOS_EXIT_SIDEWAYS_STAGNATION_MAX_PNL = 0.0035

QFOS_EXIT_MAX_HOLD_MINUTES = 45.0
QFOS_EXIT_TRAILING_PEAK_PCT = 0.0045
QFOS_EXIT_TRAILING_FLOOR_PCT = 0.0015
QFOS_EXIT_BREAKEVEN_PEAK_PCT = 0.0035
QFOS_EXIT_BREAKEVEN_FLOOR_PCT = 0.0002

QFOS_EXIT_DAEMON_INTERVAL_SECONDS = 12.0
QFOS_EXIT_MIN_SELL_NOTIONAL_USD = 0.0


def qfos_exit_lifecycle_ensure_tables():
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS qfos_exit_lifecycle_state (
                    symbol TEXT PRIMARY KEY,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    peak_pnl_pct DOUBLE PRECISION DEFAULT 0,
                    last_pnl_pct DOUBLE PRECISION DEFAULT 0,
                    last_age_min DOUBLE PRECISION DEFAULT 0,
                    last_decision TEXT,
                    last_reason TEXT,
                    last_sell_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS qfos_exit_decision_audit (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol TEXT,
                    age_min DOUBLE PRECISION,
                    pnl_pct DOUBLE PRECISION,
                    peak_pnl_pct DOUBLE PRECISION,
                    decision TEXT,
                    reason TEXT,
                    quantity DOUBLE PRECISION,
                    avg_entry DOUBLE PRECISION,
                    last_price DOUBLE PRECISION,
                    regime TEXT
                )
            """))
        return True
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] ensure_tables error={e}", flush=True)
        return False


def qfos_exit_lifecycle_fetch_positions():
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                WITH buys AS (
                    SELECT
                        symbol,
                        MIN(created_at) AS first_buy_at,
                        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE -quantity END),0) AS net_qty
                    FROM trades
                    GROUP BY symbol
                )
                SELECT
                    p.symbol,
                    p.quantity,
                    p.avg_entry,
                    p.last_price,
                    p.exposure,
                    p.unrealized_pnl,
                    p.strategy,
                    b.first_buy_at,
                    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - b.first_buy_at)) / 60.0 AS age_minutes,
                    b.net_qty
                FROM positions p
                JOIN buys b ON b.symbol = p.symbol
                WHERE p.quantity > 0.00000001
                  AND b.net_qty > 0.00000001
                ORDER BY age_minutes DESC
            """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] fetch_positions error={e}", flush=True)
        return []


def qfos_exit_lifecycle_current_regime():
    try:
        r = str(globals().get("last_known_regime") or "").upper()
        if r:
            return r
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT regime
                FROM portfolio_snapshots
                ORDER BY id DESC
                LIMIT 1
            """)).mappings().first()
        if row and row.get("regime"):
            return str(row.get("regime")).upper()
    except Exception:
        pass

    return "SIDEWAYS"


def qfos_exit_lifecycle_get_peak(conn, symbol, pnl_pct):
    row = conn.execute(text("""
        SELECT peak_pnl_pct
        FROM qfos_exit_lifecycle_state
        WHERE symbol = :symbol
    """), {"symbol": symbol}).mappings().first()

    old_peak = float((row or {}).get("peak_pnl_pct") or pnl_pct or 0.0)
    peak = max(old_peak, float(pnl_pct or 0.0))

    conn.execute(text("""
        INSERT INTO qfos_exit_lifecycle_state (
            symbol, peak_pnl_pct, last_pnl_pct, updated_at
        )
        VALUES (
            :symbol, :peak, :pnl, CURRENT_TIMESTAMP
        )
        ON CONFLICT (symbol)
        DO UPDATE SET
            peak_pnl_pct = GREATEST(qfos_exit_lifecycle_state.peak_pnl_pct, EXCLUDED.peak_pnl_pct),
            last_pnl_pct = EXCLUDED.last_pnl_pct,
            updated_at = CURRENT_TIMESTAMP
    """), {"symbol": symbol, "peak": peak, "pnl": float(pnl_pct or 0.0)})

    return peak


def qfos_exit_lifecycle_strong_runner(symbol, age_min, pnl_pct, peak_pnl_pct, regime):
    try:
        r = str(regime or "").upper()

        # Strong runner means a position is meaningfully green and still near its peak.
        # It protects winners from premature time/stagnation exits, but not from hard stop-loss.
        if pnl_pct >= 0.0065 and peak_pnl_pct >= 0.0065 and (peak_pnl_pct - pnl_pct) <= 0.0015:
            return True

        if r != "SIDEWAYS" and pnl_pct >= 0.0045 and (peak_pnl_pct - pnl_pct) <= 0.0010 and age_min < 90:
            return True

        return False
    except Exception:
        return False


def qfos_exit_lifecycle_decide(symbol, age_min, pnl_pct, peak_pnl_pct, regime):
    r = str(regime or "").upper()

    take_profit = QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT if r == "SIDEWAYS" else QFOS_EXIT_TAKE_PROFIT_PCT
    stop_loss = QFOS_EXIT_SIDEWAYS_STOP_LOSS_PCT if r == "SIDEWAYS" else QFOS_EXIT_STOP_LOSS_PCT

    strong_runner = qfos_exit_lifecycle_strong_runner(symbol, age_min, pnl_pct, peak_pnl_pct, r)

    if pnl_pct >= take_profit:
        reason = "sideways_take_profit_exit" if r == "SIDEWAYS" else "take_profit_exit"
        return "SELL", reason

    if pnl_pct <= stop_loss:
        reason = "sideways_stop_loss_exit" if r == "SIDEWAYS" else "stop_loss_exit"
        return "SELL", reason

    if peak_pnl_pct >= QFOS_EXIT_TRAILING_PEAK_PCT and pnl_pct <= QFOS_EXIT_TRAILING_FLOOR_PCT:
        return "SELL", "trailing_profit_exit"

    if peak_pnl_pct >= QFOS_EXIT_BREAKEVEN_PEAK_PCT and pnl_pct <= QFOS_EXIT_BREAKEVEN_FLOOR_PCT:
        return "SELL", "breakeven_protection_exit"

    if r == "SIDEWAYS":
        if (
            age_min >= QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE
            and QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_PNL <= pnl_pct <= QFOS_EXIT_SIDEWAYS_STAGNATION_MAX_PNL
        ):
            if strong_runner:
                return "HOLD", "hold_runner_conditions_true"
            return "SELL", "sideways_stagnation_exit"

    if age_min >= QFOS_EXIT_MAX_HOLD_MINUTES:
        if strong_runner:
            return "HOLD", "hold_runner_conditions_true"
        reason = "sideways_max_hold_exit" if r == "SIDEWAYS" else "max_hold_exit"
        return "SELL", reason

    # Explain HOLD clearly.
    if age_min < min(QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE, QFOS_EXIT_MAX_HOLD_MINUTES):
        return "HOLD", "hold_not_old_enough"

    if pnl_pct < take_profit and pnl_pct > stop_loss:
        return "HOLD", "hold_exit_threshold_not_met"

    if pnl_pct < take_profit:
        return "HOLD", "hold_take_profit_not_hit"

    if pnl_pct > stop_loss:
        return "HOLD", "hold_stop_loss_not_hit"

    return "HOLD", "hold_exit_threshold_not_met"


def qfos_exit_lifecycle_recent_sell_exists(conn, symbol):
    row = conn.execute(text("""
        SELECT COUNT(*) AS n
        FROM trades
        WHERE symbol = :symbol
          AND lower(side) = 'sell'
          AND created_at >= CURRENT_TIMESTAMP - interval '2 minutes'
    """), {"symbol": symbol}).mappings().first()

    return int((row or {}).get("n") or 0) > 0


def qfos_exit_lifecycle_net_open_qty(conn, symbol):
    row = conn.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END),0) -
            COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END),0) AS net_qty
        FROM trades
        WHERE symbol = :symbol
    """), {"symbol": symbol}).mappings().first()
    return float((row or {}).get("net_qty") or 0.0)


def qfos_exit_lifecycle_log_decision(conn, symbol, age_min, pnl_pct, peak_pnl_pct, decision, reason, qty, avg_entry, last_price, regime):
    try:
        conn.execute(text("""
            INSERT INTO qfos_exit_decision_audit (
                symbol, age_min, pnl_pct, peak_pnl_pct, decision, reason,
                quantity, avg_entry, last_price, regime
            )
            VALUES (
                :symbol, :age_min, :pnl_pct, :peak_pnl_pct, :decision, :reason,
                :quantity, :avg_entry, :last_price, :regime
            )
        """), {
            "symbol": symbol,
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "decision": decision,
            "reason": reason,
            "quantity": qty,
            "avg_entry": avg_entry,
            "last_price": last_price,
            "regime": regime,
        })

        conn.execute(text("""
            INSERT INTO qfos_exit_lifecycle_state (
                symbol, peak_pnl_pct, last_pnl_pct, last_age_min,
                last_decision, last_reason, updated_at
            )
            VALUES (
                :symbol, :peak_pnl_pct, :pnl_pct, :age_min,
                :decision, :reason, CURRENT_TIMESTAMP
            )
            ON CONFLICT (symbol)
            DO UPDATE SET
                peak_pnl_pct = GREATEST(qfos_exit_lifecycle_state.peak_pnl_pct, EXCLUDED.peak_pnl_pct),
                last_pnl_pct = EXCLUDED.last_pnl_pct,
                last_age_min = EXCLUDED.last_age_min,
                last_decision = EXCLUDED.last_decision,
                last_reason = EXCLUDED.last_reason,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "symbol": symbol,
            "peak_pnl_pct": peak_pnl_pct,
            "pnl_pct": pnl_pct,
            "age_min": age_min,
            "decision": decision,
            "reason": reason,
        })
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] audit_write symbol={symbol} error={e}", flush=True)

    print(
        f"[EXIT_DECISION] symbol={symbol} "
        f"age_min={age_min:.2f} pnl_pct={pnl_pct:.5f} peak_pnl_pct={peak_pnl_pct:.5f} "
        f"decision={decision} reason={reason}",
        flush=True,
    )


def qfos_exit_lifecycle_build_sell_fill(symbol, qty, price, reason):
    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": float(qty),
        "expected_price": float(price),
        "fill_price": float(price),
        "slippage_bps": 0.0,
        "strategy": str(reason),
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "is_exit": True,
        "exit_reason": str(reason),
        "source": "qfos_exit_lifecycle",
    }


def qfos_exit_lifecycle_execute_sell(symbol, qty, price, reason):
    if qty <= 0 or price <= 0:
        print(
            f"[EXIT_SELL_REJECT] symbol={symbol} reason=invalid_qty_or_price qty={qty} price={price} exit_reason={reason}",
            flush=True,
        )
        return False

    notional = qty * price
    if notional < QFOS_EXIT_MIN_SELL_NOTIONAL_USD:
        print(
            f"[EXIT_SELL_REJECT] symbol={symbol} reason=below_min_notional qty={qty} price={price} notional={notional:.8f}",
            flush=True,
        )
        return False

    try:
        with engine.begin() as conn:
            net_qty = qfos_exit_lifecycle_net_open_qty(conn, symbol)
            if net_qty <= 0.00000001:
                print(
                    f"[EXIT_SELL_REJECT] symbol={symbol} reason=no_open_position ledger_net_qty={net_qty:.12f}",
                    flush=True,
                )
                return False

            if qfos_exit_lifecycle_recent_sell_exists(conn, symbol):
                print(
                    f"[EXIT_SELL_REJECT] symbol={symbol} reason=duplicate_recent_sell_guard",
                    flush=True,
                )
                return False

            sell_qty = min(float(qty), float(net_qty))
            fill = qfos_exit_lifecycle_build_sell_fill(symbol, sell_qty, price, reason)

            # Existing atomic persistence owns Agent 5 accounting invariants:
            # SELL row, is_exit, exit_reason, position reduction, cash credit, PnL.
            try:
                result = qfos_persist_fill_atomic(conn, fill, source="qfos_exit_lifecycle")
            except TypeError:
                result = qfos_persist_fill_atomic(conn, fill)

            if result is None or result is False:
                print(
                    f"[EXIT_SELL_REJECT] symbol={symbol} reason=atomic_persistence_rejected exit_reason={reason}",
                    flush=True,
                )
                return False

            conn.execute(text("""
                UPDATE qfos_exit_lifecycle_state
                SET last_sell_at = CURRENT_TIMESTAMP,
                    last_decision = 'SELL',
                    last_reason = :reason,
                    updated_at = CURRENT_TIMESTAMP
                WHERE symbol = :symbol
            """), {"symbol": symbol, "reason": reason})

            print(
                f"[EXIT_SELL_APPLIED] symbol={symbol} qty={sell_qty:.12f} price={price:.12f} "
                f"exit_reason={reason} is_exit=true",
                flush=True,
            )
            return True

    except Exception as e:
        print(f"[EXIT_SELL_ERROR] symbol={symbol} reason={reason} error={e}", flush=True)
        return False


def qfos_exit_lifecycle_evaluate_once(source="cycle"):
    qfos_exit_lifecycle_ensure_tables()

    regime = qfos_exit_lifecycle_current_regime()
    positions = qfos_exit_lifecycle_fetch_positions()

    if not positions:
        print(f"[EXIT_DECISION] symbol=ALL decision=HOLD reason=no_open_positions source={source}", flush=True)
        return 0

    sells = 0

    for p in positions:
        try:
            symbol = str(p.get("symbol") or "")
            qty = float(p.get("quantity") or 0.0)
            avg_entry = float(p.get("avg_entry") or 0.0)
            last_price = float(p.get("last_price") or 0.0)
            age_min = float(p.get("age_minutes") or 0.0)

            if not symbol or qty <= 0 or avg_entry <= 0 or last_price <= 0:
                print(
                    f"[EXIT_DECISION] symbol={symbol or 'UNKNOWN'} age_min={age_min:.2f} "
                    f"pnl_pct=0.00000 peak_pnl_pct=0.00000 decision=HOLD reason=hold_invalid_position_data",
                    flush=True,
                )
                continue

            pnl_pct = (last_price - avg_entry) / avg_entry

            with engine.begin() as conn:
                peak_pnl_pct = qfos_exit_lifecycle_get_peak(conn, symbol, pnl_pct)
                decision, reason = qfos_exit_lifecycle_decide(symbol, age_min, pnl_pct, peak_pnl_pct, regime)

                qfos_exit_lifecycle_log_decision(
                    conn=conn,
                    symbol=symbol,
                    age_min=age_min,
                    pnl_pct=pnl_pct,
                    peak_pnl_pct=peak_pnl_pct,
                    decision=decision,
                    reason=reason,
                    qty=qty,
                    avg_entry=avg_entry,
                    last_price=last_price,
                    regime=regime,
                )

            if decision == "SELL":
                if qfos_exit_lifecycle_execute_sell(symbol, qty, last_price, reason):
                    sells += 1

        except Exception as e:
            print(f"[EXIT_DECISION_ERROR] position_eval error={e} payload={p}", flush=True)

    return sells


def qfos_exit_lifecycle_start_daemon():
    try:
        import threading
        import time

        if globals().get("_qfos_exit_lifecycle_daemon_started"):
            return

        globals()["_qfos_exit_lifecycle_daemon_started"] = True

        def _worker():
            print("[EXIT_LIFECYCLE] daemon_started", flush=True)
            while True:
                try:
                    qfos_exit_lifecycle_evaluate_once(source="daemon")
                except Exception as e:
                    print(f"[EXIT_DECISION_ERROR] daemon_loop error={e}", flush=True)
                time.sleep(QFOS_EXIT_DAEMON_INTERVAL_SECONDS)

        t = threading.Thread(target=_worker, name="qfos_exit_lifecycle", daemon=True)
        t.start()
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] daemon_start error={e}", flush=True)


# ============================================================
# End QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1
# ============================================================
'''

# Insert after atomic persistence exists if possible, otherwise before bot loop helpers.
if "def send_auto_pause(" in text:
    text = text.replace("def send_auto_pause(", helper + "\n\ndef send_auto_pause(", 1)
else:
    text += "\n\n" + helper + "\n"

# Start daemon after DB wait and positions table setup. Prefer the existing ensure_positions_table() call.
start_call = """
try:
    qfos_exit_lifecycle_start_daemon()
except Exception as e:
    print(f'[EXIT_DECISION_ERROR] startup_call error={e}', flush=True)
"""

if "qfos_exit_lifecycle_start_daemon()" not in text.split("QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1", 1)[-1]:
    anchor = "ensure_positions_table()"
    if anchor in text:
        text = text.replace(anchor, anchor + "\n" + start_call, 1)
    else:
        text += "\n" + start_call + "\n"

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
