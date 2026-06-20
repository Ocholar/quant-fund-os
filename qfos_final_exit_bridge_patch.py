from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_FINAL_EXIT_BRIDGE_ACTIVE_CALLSITE_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_FINAL_EXIT_BRIDGE_ACTIVE_CALLSITE_V1
# Purpose:
#   The exit lifecycle can generate DB-backed SELLs, and Agent 5 can now
#   DB-confirm/bypass FULL_PROFIT_MODE. But some cycles reach execution with
#   proposed_fills=0, so no SELLs reach Agent 5.
#
#   This bridge injects DB-backed exit SELLs directly at the active execution
#   callsite before Agent 5 filtering.
#
# Scope:
#   - Does not change BUY logic.
#   - Does not change accounting.
#   - Does not change feature generation.
#   - Only adds qualified DB exit SELLs before execution filtering.

def qfos_final_exit_bridge_add_db_sells(applied_fills, regime):
    fills = list(applied_fills or [])

    try:
        if "qfos_exit_lifecycle_db_sells" not in globals():
            print("[FINAL_EXIT_BRIDGE] exit_lifecycle_function_missing", flush=True)
            return fills

        db_sells = qfos_exit_lifecycle_db_sells(regime)

        if not db_sells:
            print("[FINAL_EXIT_BRIDGE] db_sells=0", flush=True)
            return fills

        existing_sell_symbols = set()

        for fill in fills:
            try:
                if str(fill.get("side", "")).lower() == "sell":
                    existing_sell_symbols.add(str(fill.get("symbol", "")).strip())
            except Exception:
                pass

        added = []

        for sell in db_sells:
            try:
                symbol = str(sell.get("symbol", "")).strip()
                if not symbol:
                    continue

                # One exit per symbol per cycle.
                if symbol in existing_sell_symbols:
                    print(
                        f"[FINAL_EXIT_BRIDGE] duplicate_suppressed symbol={symbol}",
                        flush=True,
                    )
                    continue

                sell = dict(sell)
                reason = str(
                    sell.get("exit_reason")
                    or sell.get("reason")
                    or sell.get("strategy")
                    or "exit_lifecycle"
                ).strip()

                sell["side"] = "sell"
                sell["is_exit"] = True
                sell["exit_reason"] = reason
                sell["reason"] = reason
                sell["strategy"] = reason
                sell["source"] = sell.get("source") or "final_exit_bridge"

                added.append(sell)
                existing_sell_symbols.add(symbol)

            except Exception as exc:
                print(
                    "[FINAL_EXIT_BRIDGE] add_sell_error "
                    + repr(exc),
                    flush=True,
                )

        if added:
            print(
                "[FINAL_EXIT_BRIDGE] added_db_exit_sells="
                + str([(x.get("symbol"), x.get("quantity"), x.get("exit_reason")) for x in added]),
                flush=True,
            )

        return added + fills

    except Exception as exc:
        print("[FINAL_EXIT_BRIDGE] bridge_error " + repr(exc), flush=True)
        return fills

# END QFOS_FINAL_EXIT_BRIDGE_ACTIVE_CALLSITE_V1
'''

# Insert helper before main execution region.
anchor = "def entry_quality_ranked_symbols"
idx = text.find(anchor)

if idx == -1:
    anchor = "def total_exposure"
    idx = text.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: helper insertion anchor not found")

text = text[:idx] + helper + "\n\n" + text[idx:]

# Patch the active execution callsite.
candidates = [
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_final_exit_bridge_add_db_sells(applied_fills, regime)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_final_exit_bridge_add_db_sells(applied_fills, regime)
                applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
    (
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
""",
        """                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = qfos_final_exit_bridge_add_db_sells(applied_fills, regime)
                applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)
                for fill in applied_fills:
""",
    ),
]

patched = False

for old, new in candidates:
    if old in text:
        text = text.replace(old, new, 1)
        patched = True
        break

if not patched:
    raise SystemExit("PATCH_FAILED: active execution callsite not found")

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
