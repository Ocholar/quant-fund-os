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

def _qfos_cleanup_closed_symbol_runtime_state(symbol, reason="closed_or_duplicate_sell", source="unknown"):
    """
    Clears stale runtime/profit-engine state for a symbol after the DB proves
    the symbol is already closed or the latest trade is already a SELL.

    This prevents Profit Engine / watchdog loops from repeatedly requesting
    the same invalid duplicate SELL.
    """
    removed = []

    dict_names = [
        "profit_engine_state",
        "profit_engine_peaks",
        "qfos_profit_engine_state",
        "qfos_profit_engine_peaks",
        "_qfos_profit_engine_state",
        "_qfos_profit_engine_peaks",
        "profit_engine_positions",
        "position_profit_state",
        "position_peaks",
        "pe_state",
        "peaks",
        "position_tracking_cache",
        "qfos_position_tracking_cache",
        "watchdog_state",
        "position_watchdog_state",
        "qfos_watchdog_state",
    ]

    set_names = [
        "profit_engine_active_symbols",
        "qfos_profit_engine_active_symbols",
        "position_watchdog_symbols",
        "qfos_position_watchdog_symbols",
        "closing_symbols",
        "symbols_pending_close",
        "qfos_symbols_pending_close",
    ]

    g = globals()

    for name in dict_names:
        obj = g.get(name)
        if isinstance(obj, dict) and symbol in obj:
            try:
                obj.pop(symbol, None)
                removed.append(name)
            except Exception:
                pass

    for name in set_names:
        obj = g.get(name)
        if isinstance(obj, set) and symbol in obj:
            try:
                obj.discard(symbol)
                removed.append(name)
            except Exception:
                pass

    if removed:
        _qfos_log_atomic(
            "[QFOS_RUNTIME_STATE_CLEANUP] symbol=%s reason=%s source=%s cleared=%s"
            % (symbol, reason, source, ",".join(sorted(set(removed))))
        )
    else:
        _qfos_log_atomic(
            "[QFOS_RUNTIME_STATE_CLEANUP] symbol=%s reason=%s source=%s cleared=none"
            % (symbol, reason, source)
        )

    return removed


def _qfos_db_open_qty_for_symbol(conn, symbol):
    try:
        pos = _qfos_get_position_row(conn, symbol)
        return _qfos_float(pos.get("quantity"), 0.0)
    except Exception:
        return None


def _qfos_latest_trade_is_sell_and_no_open_qty(conn, symbol):
    """
    True when DB says:
    - latest trade for symbol is SELL
    - position quantity is zero or missing

    Used by Profit Engine / watchdog source guards to avoid producing
    duplicate SELL requests after the symbol is already closed.
    """
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return False

    if latest.get("side") != "sell":
        return False

    open_qty = _qfos_db_open_qty_for_symbol(conn, symbol)
    if open_qty is None:
        return False

    return open_qty <= _QFOS_EPSILON
'''

if "def _qfos_cleanup_closed_symbol_runtime_state(" not in block:
    insert_at = block.find("\ndef qfos_persist_fill_atomic")
    if insert_at == -1:
        raise SystemExit("FAIL: qfos_persist_fill_atomic not found in atomic block")
    block = block[:insert_at] + helper + block[insert_at:]
    print("Inserted runtime-state cleanup helpers inside atomic boundary.")
else:
    print("Runtime-state cleanup helpers already present.")

# Make duplicate_latest_sell rejection clear stale runtime state before returning.
old = '''            if dup:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s strategy=%s reason=%s latest_id=%s source=%s"
                    % (symbol, requested_qty, strategy, dup.get("reason"), dup.get("latest_id"), source)
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None
'''

new = '''            if dup:
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
    print("Patched duplicate_latest_sell rejection to clear runtime state.")
elif "_qfos_cleanup_closed_symbol_runtime_state(" in block and "reason=\"duplicate_latest_sell\"" in block:
    print("duplicate_latest_sell cleanup already patched.")
else:
    print("WARNING: exact duplicate_latest_sell rejection block not found; source guard will still be patched.")

s = s[:m.start()] + block + s[m.end():]

# Source-side guard for _qfos_pe_sell if present.
pe_pat = re.compile(r"(?m)^def\s+_qfos_pe_sell\s*\((.*?)\):")
pe_match = pe_pat.search(s)

if pe_match:
    func_start = pe_match.end()
    next_def = re.search(r"(?m)^(def|class)\s+\w+", s[func_start:])
    func_end = func_start + next_def.start() if next_def else len(s)
    func_body = s[func_start:func_end]

    if "PHASE2D_PE_DUPLICATE_SELL_SOURCE_GUARD" not in func_body:
        # Try to infer parameter names.
        signature = pe_match.group(1)
        params = [x.strip().split("=")[0].strip() for x in signature.split(",") if x.strip()]
        symbol_expr = "symbol" if "symbol" in params else None
        conn_expr = "conn" if "conn" in params else None

        # Fallbacks: most local functions use symbol and conn names internally.
        if symbol_expr is None:
            symbol_expr = "symbol"
        if conn_expr is None:
            conn_expr = "conn"

        guard = f'''
    # PHASE2D_PE_DUPLICATE_SELL_SOURCE_GUARD
    try:
        if _qfos_latest_trade_is_sell_and_no_open_qty({conn_expr}, {symbol_expr}):
            _qfos_cleanup_closed_symbol_runtime_state(
                {symbol_expr},
                reason="pe_source_latest_sell_no_open_qty",
                source="_qfos_pe_sell",
            )
            print(
                "[PE_SELL_SOURCE_SKIP] symbol=%s reason=latest_sell_no_open_qty"
                % ({symbol_expr},),
                flush=True,
            )
            return None
    except Exception as _phase2d_guard_error:
        print(
            "[PE_SELL_SOURCE_GUARD_ERROR] symbol=%s error=%s"
            % ({symbol_expr} if "{symbol_expr}" in locals() else "unknown", repr(_phase2d_guard_error)),
            flush=True,
        )
'''
        s = s[:func_start] + guard + s[func_start:]
        print("Inserted source-side guard into _qfos_pe_sell().")
    else:
        print("_qfos_pe_sell source guard already present.")
else:
    print("WARNING: _qfos_pe_sell() not found; atomic cleanup still patched.")

# Add generic standalone cleanup function outside atomic block if main flow wants to call it by name.
# The actual implementation lives inside the atomic block, so no duplicate function is added.

p.write_text(s, encoding="utf-8")
print("Phase 2D duplicate SELL request source patch complete.")
