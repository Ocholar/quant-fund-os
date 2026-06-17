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

def _qfos_reconcile_position_from_duplicate_latest_sell(
    conn,
    symbol,
    requested_qty,
    existing_qty,
    existing_avg,
    existing_realized,
    fill_price,
    strategy,
    source,
    dup,
):
    """
    Fixes stale DB position after a SELL trade already exists.

    If latest trade is already a SELL for this symbol and the DB still shows
    open quantity, this is not a new SELL. It is a reconciliation event:
    zero the position and insert no new trade row.
    """
    latest_qty = _qfos_float(dup.get("latest_qty"), 0.0)
    qty_tol = max(_QFOS_EPSILON, abs(existing_qty) * 1e-9)

    if existing_qty <= _QFOS_EPSILON:
        return False

    # Accept either exact latest trade quantity or current requested quantity
    # as proof that the already-recorded SELL covers the stale open position.
    latest_covers_open = latest_qty >= existing_qty - qty_tol
    request_covers_open = requested_qty >= existing_qty - qty_tol

    if not (latest_covers_open or request_covers_open):
        return False

    _qfos_upsert_position_atomic(
        conn=conn,
        symbol=symbol,
        fill_price=fill_price,
        strategy=strategy,
        new_qty=0.0,
        new_avg_entry=existing_avg,
        new_realized_pnl=existing_realized,
    )

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="reconciled_duplicate_latest_sell_position_zeroed",
        source=source,
    )

    _qfos_log_atomic(
        "[SELL_POSITION_RECONCILED_FROM_LATEST_SELL] symbol=%s existing_qty=%s requested_qty=%s latest_qty=%s strategy=%s latest_id=%s source=%s"
        % (
            symbol,
            existing_qty,
            requested_qty,
            latest_qty,
            strategy,
            dup.get("latest_id"),
            source,
        )
    )

    return True
'''

if "def _qfos_reconcile_position_from_duplicate_latest_sell(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted reconciliation helper.")
else:
    print("Reconciliation helper already present.")

old = '''            if dup:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s strategy=%s reason=%s latest_id=%s source=%s"
                    % (symbol, requested_qty, strategy, dup.get("reason"), dup.get("latest_id"), source)
                )
                _qfos_cleanup_closed_symbol_runtime_state(
                    symbol,
                    reason="duplicate_latest_sell",
                    source=source,
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None
'''

new = '''            if dup:
                reconciled = _qfos_reconcile_position_from_duplicate_latest_sell(
                    conn=conn,
                    symbol=symbol,
                    requested_qty=requested_qty,
                    existing_qty=existing_qty,
                    existing_avg=existing_avg,
                    existing_realized=existing_realized,
                    fill_price=fill_price,
                    strategy=strategy,
                    source=source,
                    dup=dup,
                )

                if reconciled:
                    if started_tx:
                        _qfos_commit(conn)
                    return None

                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s strategy=%s reason=%s latest_id=%s source=%s"
                    % (symbol, requested_qty, strategy, dup.get("reason"), dup.get("latest_id"), source)
                )
                _qfos_cleanup_closed_symbol_runtime_state(
                    symbol,
                    reason="duplicate_latest_sell",
                    source=source,
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None
'''

if old in block:
    block = block.replace(old, new, 1)
    print("Patched duplicate_latest_sell branch to reconcile stale DB positions.")
elif "_qfos_reconcile_position_from_duplicate_latest_sell(" in block and "SELL_POSITION_RECONCILED_FROM_LATEST_SELL" in block:
    print("Reconciliation branch appears already patched.")
else:
    raise SystemExit("FAIL: duplicate_latest_sell branch not found for patching")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")

print("Phase 2E reconciliation patch complete.")
