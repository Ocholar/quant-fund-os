from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_DIRECT_EXIT_PREP_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT5_DIRECT_EXIT_PREP_V1
# Agent 5 direct execution-path patch.
# Purpose:
#   FULL_PROFIT_MODE currently rejects exit lifecycle SELLs as no-open-position
#   even when Postgres positions has quantity.
#
#   This helper prepares exit SELLs directly at the call site immediately before
#   _qfos_full_exit_filter_fills(applied_fills), so the final active filter sees
#   DB-confirmed quantity, is_exit=true, and exit_reason populated.

def _qfos_agent5_direct_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_direct_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_direct_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_direct_exit_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_direct_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_direct_side(fill) != "sell":
        return False

    reason = _qfos_agent5_direct_exit_reason(fill)

    if bool(fill.get("is_exit")):
        return True

    tokens = (
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

    return any(t in reason for t in tokens)


def _qfos_agent5_direct_db_position(symbol):
    if not symbol:
        return None

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select
                        symbol,
                        quantity,
                        avg_entry,
                        coalesce(last_price, avg_entry) as last_price
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return None

            return dict(row)
    except Exception as exc:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_direct_prepare_fill(fill):
    if not _qfos_agent5_direct_is_exit_sell(fill):
        return fill

    symbol = _qfos_agent5_direct_symbol(fill)
    pos = _qfos_agent5_direct_db_position(symbol)

    if not pos:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] no_db_open_position "
            f"symbol={symbol} reason={_qfos_agent5_direct_exit_reason(fill)}",
            flush=True,
        )
        return fill

    db_qty = max(0.0, _qfos_agent5_direct_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_direct_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] zero_sell_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return fill

    reason = _qfos_agent5_direct_exit_reason(fill) or "exit_lifecycle"

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_direct_exit_prep"

    # Make price fields consistent with DB if caller omitted one.
    db_price = _qfos_agent5_direct_float(pos.get("last_price"), 0.0)
    if db_price > 0:
        out["fill_price"] = _qfos_agent5_direct_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_direct_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_direct_float(out.get("price"), db_price) or db_price

    print(
        f"[AGENT5_DIRECT_EXIT_PREP] prepared "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return out


def qfos_agent5_direct_prepare_exit_sells(fills):
    prepared = []

    for fill in list(fills or []):
        try:
            prepared.append(_qfos_agent5_direct_prepare_fill(fill))
        except Exception as exc:
            print(
                "[AGENT5_DIRECT_EXIT_PREP] fill_prepare_error "
                + repr(exc),
                flush=True,
            )
            prepared.append(fill)

    return prepared

# END QFOS_AGENT5_DIRECT_EXIT_PREP_V1
'''

# Insert helper before the main loop region, after core helpers are already loaded.
anchor_for_helper = "def entry_quality_ranked_symbols"
idx = text.find(anchor_for_helper)
if idx == -1:
    raise SystemExit("PATCH_FAILED: could not find helper anchor def entry_quality_ranked_symbols")

text = text[:idx] + helper + "\n\n" + text[idx:]

old = """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
"""

new = """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
"""

if old not in text:
    raise SystemExit("PATCH_FAILED: exact execution callsite not found")

text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
