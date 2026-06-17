from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    flags=re.S,
)

m = block_re.search(s)
if not m:
    raise SystemExit("FAIL: QFOS atomic block not found")

block = m.group(0)

helper = r'''

def _qfos_symbol_buy_lifecycle_qty(conn, symbol):
    """
    Returns total BUY quantity recorded in trades for this symbol.
    After a clean reset, this must be > 0 before any SELL can be accepted.
    """
    try:
        cols = _qfos_table_columns(conn, "trades")
        if not cols:
            return 0.0

        side_col = _qfos_first_existing_column(cols, ["side"])
        qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])

        if not side_col or not qty_col:
            return 0.0

        row = _qfos_exec(
            conn,
            f"""
            SELECT COALESCE(SUM({qty_col}), 0)
            FROM trades
            WHERE symbol=:symbol
              AND {side_col}='buy'
            """,
            {"symbol": symbol}
        ).fetchone()

        return _qfos_float(row[0] if row else 0.0, 0.0)
    except Exception as exc:
        _qfos_log_atomic(
            "[BUY_LIFECYCLE_CHECK_ERROR] symbol=%s error=%s"
            % (symbol, repr(exc))
        )
        return 0.0


def _qfos_symbol_sell_lifecycle_qty(conn, symbol):
    try:
        cols = _qfos_table_columns(conn, "trades")
        if not cols:
            return 0.0

        side_col = _qfos_first_existing_column(cols, ["side"])
        qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])

        if not side_col or not qty_col:
            return 0.0

        row = _qfos_exec(
            conn,
            f"""
            SELECT COALESCE(SUM({qty_col}), 0)
            FROM trades
            WHERE symbol=:symbol
              AND {side_col}='sell'
            """,
            {"symbol": symbol}
        ).fetchone()

        return _qfos_float(row[0] if row else 0.0, 0.0)
    except Exception:
        return 0.0


def _qfos_has_valid_buy_lifecycle_for_sell(conn, symbol, requested_qty):
    """
    A SELL is valid only if this DB has recorded BUY lifecycle quantity.
    This prevents clean-reset SELL-only rows caused by paper_position_sync.
    """
    buy_qty = _qfos_symbol_buy_lifecycle_qty(conn, symbol)
    sell_qty = _qfos_symbol_sell_lifecycle_qty(conn, symbol)
    remaining_lifecycle_qty = max(buy_qty - sell_qty, 0.0)

    qty_tol = max(_QFOS_EPSILON, abs(requested_qty) * 1e-9)

    return buy_qty > _QFOS_EPSILON and remaining_lifecycle_qty + qty_tol > 0.0


def _qfos_zero_invalid_no_buy_position(conn, symbol, existing_avg, existing_realized, fill_price, strategy, source):
    """
    If paper_position_sync resurrected a position with no BUY lifecycle,
    zero it without creating a trade row.
    """
    _qfos_upsert_position_atomic(
        conn=conn,
        symbol=symbol,
        fill_price=fill_price,
        strategy=strategy or "invalid_no_buy_lifecycle_zeroed",
        new_qty=0.0,
        new_avg_entry=existing_avg,
        new_realized_pnl=existing_realized,
    )

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="invalid_no_buy_lifecycle_zeroed",
        source=source,
    )

    _qfos_log_atomic(
        "[SELL_VALIDATION_REJECT] symbol=%s reason=no_buy_lifecycle_position_zeroed strategy=%s source=%s"
        % (symbol, strategy, source)
    )


def qfos_reconcile_positions_without_buy_lifecycle(conn, source="no_buy_lifecycle_reconciler"):
    """
    Sweeps DB positions. Any positive position with no BUY lifecycle in trades
    is invalid after a clean reset and is zeroed with no trade row.
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

    fixed = []

    for row in rows:
        symbol = row[0]
        open_qty = _qfos_float(row[1], 0.0)
        avg_entry = _qfos_float(row[2], 0.0)
        realized = _qfos_float(row[3], 0.0)
        last_price = _qfos_float(row[4], 0.0)
        strategy = str(row[5] or "no_buy_lifecycle_reconciler")

        buy_qty = _qfos_symbol_buy_lifecycle_qty(conn, symbol)

        if buy_qty > _QFOS_EPSILON:
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
            reason="db_no_buy_lifecycle_position_zeroed",
            source=source,
        )

        _qfos_log_atomic(
            "[QFOS_NO_BUY_LIFECYCLE_POSITION_ZEROED] symbol=%s open_qty=%s strategy=%s source=%s"
            % (symbol, open_qty, strategy, source)
        )

        fixed.append(symbol)

    return fixed
'''

if "def _qfos_symbol_buy_lifecycle_qty(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted BUY-lifecycle guard helpers.")
else:
    print("BUY-lifecycle guard helpers already present.")

old = '''        if side == "sell":
            tombstone_handled = _qfos_reject_or_reconcile_tombstoned_sell('''

new = '''        if side == "sell":
            if not _qfos_has_valid_buy_lifecycle_for_sell(conn, symbol, requested_qty):
                _qfos_zero_invalid_no_buy_position(
                    conn=conn,
                    symbol=symbol,
                    existing_avg=existing_avg,
                    existing_realized=existing_realized,
                    fill_price=fill_price,
                    strategy=strategy,
                    source=source,
                )
                if started_tx:
                    _qfos_commit(conn)
                return None

            tombstone_handled = _qfos_reject_or_reconcile_tombstoned_sell('''

if old in block:
    block = block.replace(old, new, 1)
    print("Inserted no-BUY lifecycle SELL rejection before tombstone/duplicate handling.")
elif "_qfos_has_valid_buy_lifecycle_for_sell(conn, symbol, requested_qty)" in block:
    print("No-BUY lifecycle SELL guard already present.")
else:
    raise SystemExit("FAIL: could not insert no-BUY lifecycle SELL guard")

s = s[:m.start()] + block + s[m.end():]

# Patch Phase 2H daemon to also run no-BUY lifecycle reconciler.
daemon_re = re.compile(
    r"# BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1.*?# END QFOS_STALE_POSITION_RECONCILER_DAEMON_V1",
    flags=re.S,
)
dm = daemon_re.search(s)

if dm:
    daemon = dm.group(0)
    if "qfos_reconcile_positions_without_buy_lifecycle" not in daemon:
        old_daemon = '''            symbols = qfos_reconcile_stale_closed_positions(conn, source=source)
            conn.commit()
            if symbols:
                print(
                    "[QFOS_AUTO_STALE_RECONCILER] reconciled=%s source=%s"
                    % (",".join(symbols), source),
                    flush=True,
                )
            return symbols'''
        new_daemon = '''            symbols = qfos_reconcile_stale_closed_positions(conn, source=source)
            no_buy_symbols = []
            try:
                no_buy_symbols = qfos_reconcile_positions_without_buy_lifecycle(
                    conn,
                    source=source,
                )
            except Exception as _no_buy_reconcile_error:
                print(
                    "[QFOS_NO_BUY_LIFECYCLE_RECONCILER_ERROR] error=%s"
                    % repr(_no_buy_reconcile_error),
                    flush=True,
                )

            all_symbols = sorted(set((symbols or []) + (no_buy_symbols or [])))

            conn.commit()
            if all_symbols:
                print(
                    "[QFOS_AUTO_STALE_RECONCILER] reconciled=%s source=%s"
                    % (",".join(all_symbols), source),
                    flush=True,
                )
            return all_symbols'''
        if old_daemon in daemon:
            daemon = daemon.replace(old_daemon, new_daemon, 1)
            s = s[:dm.start()] + daemon + s[dm.end():]
            print("Patched auto reconciler daemon to zero no-BUY lifecycle positions.")
        else:
            print("WARNING: daemon block found but expected reconciler body was not matched.")
    else:
        print("Daemon already includes no-BUY lifecycle reconciler.")
else:
    print("WARNING: Phase 2H daemon block not found; lifecycle guard still patched in boundary.")

p.write_text(s, encoding="utf-8")
print("Phase 3A3 BUY-lifecycle guard patch complete.")
