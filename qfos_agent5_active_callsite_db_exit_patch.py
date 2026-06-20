from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT5_ACTIVE_CALLSITE_DB_EXIT_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT5_ACTIVE_CALLSITE_DB_EXIT_V1
# Agent 5 final active execution-path repair.
#
# Problem:
#   Exit lifecycle produces valid SELL fills, but the active callsite sends them
#   into _qfos_full_exit_filter_fills(), which rejects them using stale runtime
#   position memory:
#       [FULL_PROFIT_MODE] reject_sell_no_open_position
#
# Fix:
#   Immediately before FULL_PROFIT_MODE filtering:
#   - identify exit SELLs,
#   - confirm open quantity from Postgres positions,
#   - clamp SELL qty to DB open qty,
#   - set is_exit=true and exit_reason,
#   - bypass FULL_PROFIT_MODE only for DB-confirmed exit SELLs,
#   - send all other fills through the original filter.

def _qfos_agent5_active_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_active_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_active_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_active_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_active_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_active_side(fill) != "sell":
        return False

    reason = _qfos_agent5_active_reason(fill)

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
        "adaptive_take_profit",
        "adaptive_stop_loss",
        "exit",
    )

    return any(t in reason for t in tokens)


def _qfos_agent5_active_db_position(symbol):
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
            f"[AGENT5_ACTIVE_DB_EXIT] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_active_confirm_exit_sell(fill):
    if not _qfos_agent5_active_is_exit_sell(fill):
        return None

    symbol = _qfos_agent5_active_symbol(fill)
    reason = _qfos_agent5_active_reason(fill) or "exit_lifecycle"

    pos = _qfos_agent5_active_db_position(symbol)
    if not pos:
        print(
            f"[AGENT5_ACTIVE_DB_EXIT] no_db_open_position "
            f"symbol={symbol} reason={reason}",
            flush=True,
        )
        return None

    db_qty = max(0.0, _qfos_agent5_active_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_active_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_ACTIVE_DB_EXIT] zero_sell_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return None

    db_price = _qfos_agent5_active_float(pos.get("last_price"), 0.0)

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_active_db_exit"

    if db_price > 0:
        out["fill_price"] = _qfos_agent5_active_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_active_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_active_float(out.get("price"), db_price) or db_price

    # Keep runtime memory aligned for downstream execution code that still checks portfolio.positions.
    try:
        if "portfolio" in globals() and hasattr(portfolio, "positions"):
            portfolio.positions[symbol] = db_qty
    except Exception:
        pass

    print(
        f"[AGENT5_ACTIVE_DB_EXIT] bypass_confirmed "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} reason={reason}",
        flush=True,
    )

    return out


def qfos_agent5_active_filter_exit_sells(fills):
    db_exit_sells = []
    normal_fills = []
    seen_symbols = set()

    for fill in list(fills or []):
        confirmed = None

        try:
            confirmed = _qfos_agent5_active_confirm_exit_sell(fill)
        except Exception as exc:
            print(
                "[AGENT5_ACTIVE_DB_EXIT] confirm_error "
                + repr(exc),
                flush=True,
            )

        if confirmed:
            symbol = _qfos_agent5_active_symbol(confirmed)

            if symbol in seen_symbols:
                print(
                    f"[AGENT5_ACTIVE_DB_EXIT] duplicate_exit_suppressed "
                    f"symbol={symbol} reason={_qfos_agent5_active_reason(confirmed)}",
                    flush=True,
                )
                continue

            seen_symbols.add(symbol)
            db_exit_sells.append(confirmed)
        else:
            normal_fills.append(fill)

    try:
        filtered_normal = _qfos_full_exit_filter_fills(normal_fills)
    except Exception as exc:
        print(
            "[AGENT5_ACTIVE_DB_EXIT] original_filter_failed "
            + repr(exc),
            flush=True,
        )
        filtered_normal = normal_fills

    result = list(db_exit_sells or []) + list(filtered_normal or [])

    print(
        f"[AGENT5_ACTIVE_DB_EXIT] result "
        f"db_exit_sells={len(db_exit_sells)} "
        f"normal_in={len(normal_fills)} "
        f"normal_out={len(filtered_normal or [])} "
        f"total_out={len(result)}",
        flush=True,
    )

    return result

# END QFOS_AGENT5_ACTIVE_CALLSITE_DB_EXIT_V1
'''

# Insert helper before the active main loop section.
# The active callsite is later around applied_fills normalization/filtering.
anchor = "def entry_quality_ranked_symbols"
idx = text.find(anchor)

if idx == -1:
    anchor = "def total_exposure"
    idx = text.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: could not find safe helper insertion anchor")

text = text[:idx] + helper + "\n\n" + text[idx:]

# Replace the active callsite. Support both current and previously patched variants.
replacements = [
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_filter_with_db_exit_bypass(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
]

patched = False
for old, new in replacements:
    if old in text:
        text = text.replace(old, new, 1)
        patched = True
        break

if not patched:
    raise SystemExit("PATCH_FAILED: active execution filter callsite not found")

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
