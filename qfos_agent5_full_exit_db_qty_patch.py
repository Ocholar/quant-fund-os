from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_FULL_EXIT_DB_QTY_PATCH_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_AGENT5_FULL_EXIT_DB_QTY_PATCH_V1
# Agent 5 — SELL execution/filter authority
# Problem:
#   DB has open positions, but FULL_PROFIT_MODE rejects exit lifecycle SELLs
#   with reject_sell_no_open_position.
#
# Fix:
#   Before _qfos_full_exit_filter_fills rejects SELLs, normalize/clamp exit
#   SELL quantity from the DB positions table. DB open quantity is the authority.

def _qfos_agent5_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_side(fill) != "sell":
        return False

    reason = str(
        fill.get("exit_reason")
        or fill.get("reason")
        or fill.get("strategy")
        or ""
    ).strip()

    if bool(fill.get("is_exit")):
        return True

    exit_tokens = (
        "take_profit",
        "stop_loss",
        "stagnation",
        "max_hold",
        "trailing",
        "breakeven",
        "time_stop",
        "risk_off",
        "exit",
    )

    return any(tok in reason for tok in exit_tokens)


def _qfos_agent5_db_open_qty(symbol):
    if not symbol:
        return 0.0

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select quantity
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return 0.0

            return max(0.0, _qfos_agent5_float(row.get("quantity")))
    except Exception as exc:
        print(
            f"[AGENT5_FULL_EXIT_DB_QTY] db_open_qty_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return 0.0


def _qfos_agent5_prepare_exit_sell(fill):
    if not _qfos_agent5_is_exit_sell(fill):
        return fill

    symbol = _qfos_agent5_symbol(fill)
    db_qty = _qfos_agent5_db_open_qty(symbol)

    if db_qty <= 0:
        return fill

    requested_qty = _qfos_agent5_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )

    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        return fill

    reason = str(
        fill.get("exit_reason")
        or fill.get("reason")
        or fill.get("strategy")
        or "exit_lifecycle"
    ).strip()

    fill = dict(fill)
    fill["quantity"] = sell_qty
    fill["qty"] = sell_qty
    fill["is_exit"] = True
    fill["exit_reason"] = reason
    fill["reason"] = reason
    fill["strategy"] = reason
    fill["source"] = fill.get("source") or "agent5_db_qty_exit"

    print(
        f"[AGENT5_FULL_EXIT_DB_QTY] prepared_exit_sell "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return fill


def _qfos_agent5_wrap_full_exit_filter():
    global _qfos_full_exit_filter_fills

    old_filter = globals().get("_qfos_full_exit_filter_fills")

    if not callable(old_filter):
        print("[AGENT5_FULL_EXIT_DB_QTY] _qfos_full_exit_filter_fills not found", flush=True)
        return

    if getattr(old_filter, "_qfos_agent5_db_qty_wrapped", False):
        return

    def _wrapped_qfos_full_exit_filter_fills(fills):
        try:
            prepared = []
            for fill in list(fills or []):
                prepared.append(_qfos_agent5_prepare_exit_sell(fill))
        except Exception as exc:
            print(
                "[AGENT5_FULL_EXIT_DB_QTY] prepare_failed "
                + repr(exc),
                flush=True,
            )
            prepared = list(fills or [])

        return old_filter(prepared)

    _wrapped_qfos_full_exit_filter_fills._qfos_agent5_db_qty_wrapped = True
    _qfos_full_exit_filter_fills = _wrapped_qfos_full_exit_filter_fills

    print("[AGENT5_FULL_EXIT_DB_QTY] wrapped _qfos_full_exit_filter_fills", flush=True)


_qfos_agent5_wrap_full_exit_filter()

# END QFOS_AGENT5_FULL_EXIT_DB_QTY_PATCH_V1
'''

# Put this patch after the full-exit filter is defined, before main loop runtime reaches it.
anchor = "def _qfos_full_exit_filter_fills"
idx = text.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: could not find def _qfos_full_exit_filter_fills")

# Find the next major section after the function area. Safer than guessing exact function length.
next_candidates = []
for candidate in [
    "\n# ==========================================",
    "\ndef main_loop",
    "\nasync def",
    "\nwhile True:",
]:
    j = text.find(candidate, idx + len(anchor))
    if j != -1:
        next_candidates.append(j)

if not next_candidates:
    # Fallback: insert a bit later; still before runtime use in most versions.
    insert_at = idx
else:
    insert_at = min(next_candidates)

text = text[:insert_at] + patch + "\n\n" + text[insert_at:]

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
