from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "QFOS_AGENT5_EXIT_ATOMIC_PERSISTENCE_REPAIR_V1"
if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

replacement = r'''
# ============================================================
# QFOS_AGENT5_EXIT_ATOMIC_PERSISTENCE_REPAIR_V1
#
# Guarantees a public qfos_persist_fill_atomic binding for all exit paths.
# If an earlier patch renamed the implementation to
# qfos_persist_fill_atomic_core, this public alias delegates to it.
# ============================================================

if not callable(globals().get("qfos_persist_fill_atomic")):
    def qfos_persist_fill_atomic(conn, fill, source="main_loop"):
        core = globals().get("qfos_persist_fill_atomic_core")
        if not callable(core):
            raise RuntimeError(
                "atomic_persistence_helper_unavailable:"
                "qfos_persist_fill_atomic_and_core_missing"
            )
        try:
            return core(conn, fill, source=source)
        except TypeError:
            return core(conn, fill)


def qfos_exit_atomic_helper():
    public = globals().get("qfos_persist_fill_atomic")
    if callable(public):
        return "qfos_persist_fill_atomic", public

    core = globals().get("qfos_persist_fill_atomic_core")
    if callable(core):
        return "qfos_persist_fill_atomic_core", core

    return "unavailable", None


def qfos_exit_lifecycle_execute_sell(symbol, qty, price, reason):
    symbol = str(symbol or "").strip()
    requested_qty = float(qty or 0.0)
    fill_price = float(price or 0.0)
    reason = str(reason or "").strip()

    if not symbol or requested_qty <= 0 or fill_price <= 0:
        print(
            f"[EXIT_SELL_AUDIT] symbol={symbol or 'UNKNOWN'} reason={reason or 'unknown'} "
            f"decision=REJECTED reject_reason=invalid_qty_or_price "
            f"requested_qty={requested_qty} fill_price={fill_price}",
            flush=True,
        )
        return False

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT quantity
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            open_qty = float((row or {}).get("quantity") or 0.0)

            if open_qty <= 0.00000001:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=no_open_position "
                    f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                    f"fill_price={fill_price:.12f}",
                    flush=True,
                )
                return False

            if qfos_exit_lifecycle_recent_sell_exists(conn, symbol):
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=duplicate_recent_sell_guard "
                    f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                    f"fill_price={fill_price:.12f}",
                    flush=True,
                )
                return False

            sell_qty = min(requested_qty, open_qty)
            helper_name, helper = qfos_exit_atomic_helper()

            print(
                f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                f"fill_price={fill_price:.12f} persistence_helper={helper_name}",
                flush=True,
            )

            if not callable(helper):
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=atomic_helper_unavailable",
                    flush=True,
                )
                return False

            fill = qfos_exit_lifecycle_build_sell_fill(
                symbol=symbol,
                qty=sell_qty,
                price=fill_price,
                reason=reason,
            )

            # Explicitly preserve SELL exit metadata.
            fill["is_exit"] = True
            fill["exit_reason"] = reason
            fill["source"] = "qfos_exit_lifecycle"

            try:
                result = helper(conn, fill, source="qfos_exit_lifecycle")
            except TypeError:
                result = helper(conn, fill)

            if result is None or result is False:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=atomic_persistence_rejected",
                    flush=True,
                )
                return False

            latest = conn.execute(text("""
                SELECT id, quantity, fill_price, is_exit, exit_reason, pnl
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'sell'
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            if not latest:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=missing_sell_trade_after_persist",
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
                f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                f"decision=PERSISTED trade_id={latest.get('id')} "
                f"sell_qty={float(latest.get('quantity') or 0):.12f} "
                f"is_exit={latest.get('is_exit')} "
                f"exit_reason={latest.get('exit_reason')} "
                f"pnl={float(latest.get('pnl') or 0):.12f}",
                flush=True,
            )
            return True

    except Exception as e:
        print(
            f"[EXIT_SELL_ERROR] symbol={symbol} reason={reason} "
            f"error={type(e).__name__}:{e}",
            flush=True,
        )
        return False


# ============================================================
# End QFOS_AGENT5_EXIT_ATOMIC_PERSISTENCE_REPAIR_V1
# ============================================================
'''

pattern = (
    r"def qfos_exit_lifecycle_execute_sell\(symbol, qty, price, reason\):"
    r".*?"
    r"(?=\ndef qfos_exit_lifecycle_evaluate_once\()"
)

match = re.search(pattern, text, flags=re.S)
if not match:
    raise SystemExit("ERROR: qfos_exit_lifecycle_execute_sell block not found")

text = text[:match.start()] + replacement + text[match.end():]
path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
