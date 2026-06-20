from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_BYPASS_FULL_PROFIT_DB_EXITS_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT5_BYPASS_FULL_PROFIT_DB_EXITS_V1
# Agent 5 final SELL filter repair.
#
# Problem:
#   qfos_agent5_direct_prepare_exit_sells() confirms DB open quantity,
#   but _qfos_full_exit_filter_fills() still rejects the SELL with:
#       reject_sell_no_open_position
#
# Root cause:
#   FULL_PROFIT_MODE is still using stale/incomplete runtime position memory.
#
# Fix:
#   Split fills before FULL_PROFIT_MODE:
#     - DB-confirmed exit SELLs bypass _qfos_full_exit_filter_fills.
#     - Non-exit or uncertain fills still go through _qfos_full_exit_filter_fills.
#   This preserves duplicate protection while preventing stale memory from
#   blocking real DB-backed risk exits.

def _qfos_agent5_bypass_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_bypass_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_bypass_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_bypass_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_bypass_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_bypass_side(fill) != "sell":
        return False

    reason = _qfos_agent5_bypass_reason(fill)

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
        "adaptive",
    )

    return any(t in reason for t in tokens)


def _qfos_agent5_bypass_db_position(symbol):
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
            f"[AGENT5_BYPASS_FULL_PROFIT] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_bypass_confirm_exit_sell(fill):
    """
    Return normalized DB-confirmed exit sell, or None if it must remain
    under the normal FULL_PROFIT_MODE filter.
    """
    if not _qfos_agent5_bypass_is_exit_sell(fill):
        return None

    symbol = _qfos_agent5_bypass_symbol(fill)
    reason = _qfos_agent5_bypass_reason(fill) or "exit_lifecycle"

    pos = _qfos_agent5_bypass_db_position(symbol)
    if not pos:
        print(
            f"[AGENT5_BYPASS_FULL_PROFIT] cannot_bypass_no_db_position "
            f"symbol={symbol} reason={reason}",
            flush=True,
        )
        return None

    db_qty = max(0.0, _qfos_agent5_bypass_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_bypass_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_BYPASS_FULL_PROFIT] cannot_bypass_zero_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return None

    db_price = _qfos_agent5_bypass_float(pos.get("last_price"), 0.0)

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_bypass_full_profit_db_exit"

    if db_price > 0:
        out["fill_price"] = _qfos_agent5_bypass_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_bypass_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_bypass_float(out.get("price"), db_price) or db_price

    # Sync runtime memory so later execution logs/status do not see zero.
    try:
        if "portfolio" in globals() and hasattr(portfolio, "positions"):
            portfolio.positions[symbol] = sell_qty
    except Exception:
        pass

    print(
        f"[AGENT5_BYPASS_FULL_PROFIT] bypass_confirmed "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return out


def qfos_agent5_filter_with_db_exit_bypass(fills):
    """
    Replacement for direct use of _qfos_full_exit_filter_fills(applied_fills).

    DB-confirmed exit SELLs bypass FULL_PROFIT_MODE.
    All other fills still pass through FULL_PROFIT_MODE.
    """
    db_exit_sells = []
    normal_fills = []
    seen_exit_symbols = set()

    for fill in list(fills or []):
        confirmed = None
        try:
            confirmed = _qfos_agent5_bypass_confirm_exit_sell(fill)
        except Exception as exc:
            print(
                "[AGENT5_BYPASS_FULL_PROFIT] confirm_error "
                + repr(exc),
                flush=True,
            )

        if confirmed:
            symbol = _qfos_agent5_bypass_symbol(confirmed)

            # One full exit per symbol per cycle. This prevents duplicate
            # lifecycle + adaptive SELLs from both firing.
            if symbol in seen_exit_symbols:
                print(
                    f"[AGENT5_BYPASS_FULL_PROFIT] duplicate_exit_suppressed "
                    f"symbol={symbol} reason={_qfos_agent5_bypass_reason(confirmed)}",
                    flush=True,
                )
                continue

            seen_exit_symbols.add(symbol)
            db_exit_sells.append(confirmed)
        else:
            normal_fills.append(fill)

    try:
        filtered_normal = _qfos_full_exit_filter_fills(normal_fills)
    except Exception as exc:
        print(
            "[AGENT5_BYPASS_FULL_PROFIT] original_filter_failed "
            + repr(exc),
            flush=True,
        )
        filtered_normal = normal_fills

    result = list(db_exit_sells or []) + list(filtered_normal or [])

    print(
        f"[AGENT5_BYPASS_FULL_PROFIT] result "
        f"db_exit_sells={len(db_exit_sells)} "
        f"normal_in={len(normal_fills)} "
        f"normal_out={len(filtered_normal or [])} "
        f"total_out={len(result)}",
        flush=True,
    )

    return result

# END QFOS_AGENT5_BYPASS_FULL_PROFIT_DB_EXITS_V1
'''

# Insert helper before the execution loop area, near the previous Agent 5 direct helper if present.
anchor = "def qfos_agent5_direct_prepare_exit_sells"
idx = text.find(anchor)

if idx == -1:
    anchor = "def entry_quality_ranked_symbols"
    idx = text.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: could not find helper insertion anchor")

text = text[:idx] + helper + "\n\n" + text[idx:]

old = """                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
"""

new = """                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_filter_with_db_exit_bypass(applied_fills)
                for fill in applied_fills:
"""

if old not in text:
    # fallback for repo version where direct prep has not been inserted yet
    old2 = """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
"""
    new2 = """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_filter_with_db_exit_bypass(applied_fills)
                for fill in applied_fills:
"""
    if old2 not in text:
        raise SystemExit("PATCH_FAILED: execution filter callsite not found")
    text = text.replace(old2, new2, 1)
else:
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
