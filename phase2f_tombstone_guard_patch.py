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

_QFOS_CLOSED_SYMBOL_TOMBSTONES = globals().setdefault("_QFOS_CLOSED_SYMBOL_TOMBSTONES", {})


def _qfos_mark_symbol_closed(symbol, side, quantity, strategy, source):
    if side != "sell":
        return

    _QFOS_CLOSED_SYMBOL_TOMBSTONES[symbol] = {
        "quantity": float(quantity or 0.0),
        "strategy": str(strategy or ""),
        "source": str(source or ""),
        "closed_at": _qfos_now_utc_text(),
    }

    _qfos_log_atomic(
        "[QFOS_SYMBOL_CLOSED_TOMBSTONE_SET] symbol=%s qty=%s strategy=%s source=%s"
        % (symbol, quantity, strategy, source)
    )


def _qfos_clear_symbol_closed_tombstone(symbol, reason="buy_or_reopen"):
    if symbol in _QFOS_CLOSED_SYMBOL_TOMBSTONES:
        _QFOS_CLOSED_SYMBOL_TOMBSTONES.pop(symbol, None)
        _qfos_log_atomic(
            "[QFOS_SYMBOL_CLOSED_TOMBSTONE_CLEARED] symbol=%s reason=%s"
            % (symbol, reason)
        )


def _qfos_has_closed_tombstone(symbol):
    return symbol in _QFOS_CLOSED_SYMBOL_TOMBSTONES


def _qfos_latest_trade_side(conn, symbol):
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return None
    return latest.get("side")


def _qfos_reject_or_reconcile_tombstoned_sell(
    conn,
    symbol,
    requested_qty,
    existing_qty,
    existing_avg,
    existing_realized,
    fill_price,
    strategy,
    source,
):
    """
    If this process already closed the symbol and no new BUY has happened,
    do not allow another SELL row. If stale sync restored quantity, zero it.
    """
    if not _qfos_has_closed_tombstone(symbol):
        return False

    latest_side = _qfos_latest_trade_side(conn, symbol)

    # A new BUY should clear tombstone and allow normal lifecycle.
    if latest_side == "buy":
        _qfos_clear_symbol_closed_tombstone(symbol, reason="latest_trade_buy")
        return False

    if latest_side != "sell":
        return False

    if existing_qty > _QFOS_EPSILON:
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
            reason="tombstone_rezero_stale_restored_position",
            source=source,
        )
        _qfos_log_atomic(
            "[SELL_TOMBSTONE_RECONCILED_STALE_POSITION] symbol=%s existing_qty=%s requested_qty=%s strategy=%s source=%s"
            % (symbol, existing_qty, requested_qty, strategy, source)
        )
        return True

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="tombstone_duplicate_sell_no_open_qty",
        source=source,
    )
    _qfos_log_atomic(
        "[SELL_TOMBSTONE_REJECT] symbol=%s requested_qty=%s strategy=%s source=%s"
        % (symbol, requested_qty, strategy, source)
    )
    return True
'''

if "def _qfos_mark_symbol_closed(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted closed-symbol tombstone helpers.")
else:
    print("Tombstone helpers already present.")

# Insert BUY tombstone clear after side validation / before tx is okay.
if "_qfos_clear_symbol_closed_tombstone(symbol, reason=\"accepted_buy_request\")" not in block:
    target = '''    if fill_price <= _QFOS_EPSILON:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s price=%s reason=non_positive_fill_price source=%s" % (symbol, side, fill_price, source))
        return None
'''
    replacement = target + '''
    if side == "buy":
        _qfos_clear_symbol_closed_tombstone(symbol, reason="accepted_buy_request")
'''
    if target in block:
        block = block.replace(target, replacement, 1)
        print("Inserted BUY tombstone clear.")
    else:
        print("WARNING: could not insert BUY tombstone clear at expected point.")

# Insert tombstone guard after existing position variables are known and before duplicate guard.
old = '''        if side == "sell":
            dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)
'''

new = '''        if side == "sell":
            tombstone_handled = _qfos_reject_or_reconcile_tombstoned_sell(
                conn=conn,
                symbol=symbol,
                requested_qty=requested_qty,
                existing_qty=existing_qty,
                existing_avg=existing_avg,
                existing_realized=existing_realized,
                fill_price=fill_price,
                strategy=strategy,
                source=source,
            )
            if tombstone_handled:
                if started_tx:
                    _qfos_commit(conn)
                return None

            dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)
'''

if old in block:
    block = block.replace(old, new, 1)
    print("Inserted tombstone guard before duplicate SELL guard.")
elif "_qfos_reject_or_reconcile_tombstoned_sell(" in block:
    print("Tombstone guard already present.")
else:
    raise SystemExit("FAIL: could not insert tombstone guard")

# Mark tombstone after accepted SELL position update + trade insert.
old2 = '''        _qfos_insert_trade_atomic(conn, normalized)

        if started_tx:
            _qfos_commit(conn)
'''

new2 = '''        _qfos_insert_trade_atomic(conn, normalized)

        if side == "sell" and new_qty <= _QFOS_EPSILON:
            _qfos_mark_symbol_closed(
                symbol=symbol,
                side=side,
                quantity=final_qty,
                strategy=strategy,
                source=source,
            )

        if started_tx:
            _qfos_commit(conn)
'''

if old2 in block:
    block = block.replace(old2, new2, 1)
    print("Inserted tombstone set after accepted full SELL.")
elif "_qfos_mark_symbol_closed(" in block and "QFOS_SYMBOL_CLOSED_TOMBSTONE_SET" in block:
    print("Tombstone set already present.")
else:
    raise SystemExit("FAIL: could not insert tombstone set")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")

print("Phase 2F tombstone guard patch complete.")
