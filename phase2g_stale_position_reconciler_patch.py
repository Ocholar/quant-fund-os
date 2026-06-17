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
    raise SystemExit("FAIL: atomic boundary block not found")

block = m.group(0)

helper = r'''

def qfos_reconcile_stale_closed_positions(conn, source="stale_position_reconciler"):
    """
    DB-level safety sweep.

    If positions.quantity > 0 but latest trade for that symbol is SELL and
    there is no newer BUY, the position is stale/corrupt. Zero it without
    inserting a trade row.

    This catches cases where no new SELL request arrives to trigger the
    normal duplicate/tombstone reconciliation path.
    """
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        return []

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])
    last_price_col = _qfos_first_existing_column(cols, ["last_price", "mark_price", "price"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])

    if not qty_col:
        return []

    rows = _qfos_exec(
        conn,
        f"SELECT symbol, {qty_col}"
        + (f", {avg_col}" if avg_col else ", 0")
        + (f", {realized_col}" if realized_col else ", 0")
        + (f", {last_price_col}" if last_price_col else ", 0")
        + (f", {strategy_col}" if strategy_col else ", ''")
        + f" FROM positions WHERE {qty_col} > :eps",
        {"eps": _QFOS_EPSILON}
    ).fetchall()

    reconciled = []

    for row in rows:
        symbol = row[0]
        open_qty = _qfos_float(row[1], 0.0)
        avg_entry = _qfos_float(row[2], 0.0)
        realized = _qfos_float(row[3], 0.0)
        last_price = _qfos_float(row[4], 0.0)
        strategy = str(row[5] or "stale_position_reconciler")

        latest = _qfos_latest_trade_for_symbol(conn, symbol)
        if not latest:
            continue

        if latest.get("side") != "sell":
            continue

        latest_qty = _qfos_float(latest.get("quantity"), 0.0)
        qty_tol = max(_QFOS_EPSILON, abs(open_qty) * 1e-9)

        if latest_qty + qty_tol < open_qty:
            continue

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=last_price if last_price > _QFOS_EPSILON else avg_entry,
            strategy=strategy,
            new_qty=0.0,
            new_avg_entry=avg_entry,
            new_realized_pnl=realized,
        )

        _qfos_cleanup_closed_symbol_runtime_state(
            symbol,
            reason="db_stale_closed_position_reconciled",
            source=source,
        )

        _qfos_mark_symbol_closed(
            symbol=symbol,
            side="sell",
            quantity=open_qty,
            strategy=strategy,
            source=source,
        )

        _qfos_log_atomic(
            "[QFOS_DB_STALE_POSITION_RECONCILED] symbol=%s open_qty=%s latest_sell_qty=%s latest_id=%s source=%s"
            % (symbol, open_qty, latest_qty, latest.get("id"), source)
        )

        reconciled.append(symbol)

    return reconciled
'''

if "def qfos_reconcile_stale_closed_positions(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted DB stale closed-position reconciler.")
else:
    print("DB stale closed-position reconciler already present.")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")

print("Phase 2G stale-position reconciler patched.")
