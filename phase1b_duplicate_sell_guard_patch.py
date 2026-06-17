from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    re.S,
)

m = block_re.search(s)
if not m:
    raise SystemExit("FAIL: QFOS atomic block not found")

block = m.group(0)

helper = r'''

def _qfos_latest_trade_for_symbol(conn, symbol):
    cols = _qfos_table_columns(conn, "trades")
    if not cols:
        return None

    id_col = _qfos_first_existing_column(cols, ["id"])
    side_col = _qfos_first_existing_column(cols, ["side"])
    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])

    if not side_col or not qty_col:
        return None

    select_cols = []
    if id_col:
        select_cols.append(id_col)
    else:
        select_cols.append("rowid")

    select_cols.extend([side_col, qty_col])

    if strategy_col:
        select_cols.append(strategy_col)

    order_col = id_col if id_col else "rowid"

    row = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM trades WHERE symbol=? ORDER BY {order_col} DESC LIMIT 1",
        (symbol,)
    ).fetchone()

    if not row:
        return None

    out = {
        "id": row[0],
        "side": str(row[1] or "").lower(),
        "quantity": _qfos_float(row[2], 0.0),
        "strategy": "",
    }

    if strategy_col and len(row) >= 4:
        out["strategy"] = str(row[3] or "")

    return out


def _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy):
    """
    Runtime idempotency guard.

    If the latest persisted trade for this symbol is already a SELL with the
    same quantity and same strategy, reject the new SELL. This stops repeated
    full-position SELL spam even if an upstream loop keeps firing the same exit.
    """
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return None

    latest_side = latest.get("side")
    latest_qty = _qfos_float(latest.get("quantity"), 0.0)
    latest_strategy = str(latest.get("strategy") or "")
    strategy = str(strategy or "")

    qty_tol = max(_QFOS_EPSILON, abs(requested_qty) * 1e-9)

    if (
        latest_side == "sell"
        and abs(latest_qty - requested_qty) <= qty_tol
        and latest_strategy == strategy
    ):
        return {
            "reason": "duplicate_latest_sell",
            "latest_id": latest.get("id"),
            "latest_qty": latest_qty,
            "latest_strategy": latest_strategy,
        }

    return None
'''

if "def _qfos_duplicate_sell_guard(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found inside atomic block")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted _qfos_duplicate_sell_guard helper.")
else:
    print("Duplicate sell guard helper already present.")

old = '''        pos = _qfos_get_position_row(conn, symbol)
        existing_qty = _qfos_float(pos.get("quantity"), 0.0)
        existing_avg = _qfos_float(pos.get("avg_entry"), 0.0)
        existing_realized = _qfos_float(pos.get("realized_pnl"), 0.0)

        final_qty = requested_qty
        pnl = 0.0

        if side == "sell":
'''

new = '''        pos = _qfos_get_position_row(conn, symbol)
        existing_qty = _qfos_float(pos.get("quantity"), 0.0)
        existing_avg = _qfos_float(pos.get("avg_entry"), 0.0)
        existing_realized = _qfos_float(pos.get("realized_pnl"), 0.0)

        final_qty = requested_qty
        pnl = 0.0

        if side == "sell":
            dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)
            if dup:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s strategy=%s reason=%s latest_id=%s source=%s"
                    % (symbol, requested_qty, strategy, dup.get("reason"), dup.get("latest_id"), source)
                )
                if started_tx:
                    conn.rollback()
                return None
'''

if old not in block:
    if "dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)" in block:
        print("Duplicate sell guard call already present.")
    else:
        raise SystemExit("FAIL: expected insertion point not found")
else:
    block = block.replace(old, new, 1)
    print("Inserted runtime duplicate SELL guard call.")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")

print("Phase 1B duplicate SELL guard patched.")
