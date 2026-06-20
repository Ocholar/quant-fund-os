from pathlib import Path
import re

# ------------------------------------------------------------
# 1) main.py: final persistence boundary hard-block for BUYs.
# ------------------------------------------------------------
main_path = Path("main.py")
src = main_path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_PAUSE_HARD_BLOCK_ATOMIC_BUY_V1"

if marker not in src:
    match = re.search(
        r"(?m)^def\s+qfos_persist_fill_atomic\s*\([^\n]*\):\s*$",
        src,
    )
    if not match:
        raise SystemExit(
            "PATCH_FAILED: qfos_persist_fill_atomic(...) definition not found."
        )

    patch = r'''
    # QFOS_PAUSE_HARD_BLOCK_ATOMIC_BUY_V1
    # Final authority: no new BUY may persist while the bot is paused.
    # SELLs remain allowed so protective exits can still close risk.
    try:
        if isinstance(fill, dict):
            _qfos_pause_fill = fill
        else:
            _qfos_pause_fill = {
                "side": getattr(fill, "side", None),
                "symbol": getattr(fill, "symbol", None),
                "strategy": getattr(fill, "strategy", None),
            }

        _qfos_pause_side = str(
            _qfos_pause_fill.get("side") or ""
        ).strip().lower()

        if _qfos_pause_side == "buy":
            _qfos_pause_state_known = False
            _qfos_pause_active = True  # fail closed if pause authority is unavailable

            try:
                _qfos_is_paused_fn = globals().get("is_paused")
                if callable(_qfos_is_paused_fn):
                    _qfos_pause_active = bool(_qfos_is_paused_fn())
                    _qfos_pause_state_known = True
                elif "paused" in globals():
                    _qfos_pause_active = bool(globals().get("paused"))
                    _qfos_pause_state_known = True
            except Exception:
                _qfos_pause_active = True

            if _qfos_pause_active:
                print(
                    "[PAUSE_HARD_BLOCK] "
                    f"side=buy symbol={_qfos_pause_fill.get('symbol')} "
                    f"strategy={_qfos_pause_fill.get('strategy')} "
                    f"source={source} "
                    f"pause_state_known={_qfos_pause_state_known}",
                    flush=True,
                )
                return False
    except Exception as _qfos_pause_guard_error:
        print(
            "[PAUSE_HARD_BLOCK] "
            f"side=buy reason=guard_exception "
            f"error={_qfos_pause_guard_error!r} source={source}",
            flush=True,
        )
        return False
'''

    insert_at = match.end()
    src = src[:insert_at] + "\n" + patch + src[insert_at:]
    main_path.write_text(src, encoding="utf-8")
    print("MAIN_PATCH_OK: atomic BUY pause hard-block installed")
else:
    print("MAIN_PATCH_ALREADY_PRESENT")

# ------------------------------------------------------------
# 2) services/api.py: dashboard must not retain old win rate.
# ------------------------------------------------------------
api_path = Path("services/api.py")
api = api_path.read_text(encoding="utf-8", errors="replace")

api_marker = "# QFOS_DASHBOARD_TRUTHFUL_WIN_RATE_OVERRIDE_V1"

if api_marker not in api:
    expected = (
        '        performance["win_rate_estimate"] = '
        'round(float(truth["truthful_win_rate"]), 4)\n'
    )

    if expected not in api:
        raise SystemExit(
            "PATCH_FAILED: truthful win_rate_estimate assignment not found in services/api.py. "
            "No API file was changed."
        )

    replacement = (
        '        # QFOS_DASHBOARD_TRUTHFUL_WIN_RATE_OVERRIDE_V1\n'
        '        # Keep legacy and dashboard fields aligned with matched completed outcomes.\n'
        '        _qfos_truthful_rate = round(float(truth["truthful_win_rate"]), 4)\n'
        '        performance["win_rate"] = _qfos_truthful_rate\n'
        '        performance["win_rate_estimate"] = _qfos_truthful_rate\n'
    )

    api = api.replace(expected, replacement, 1)

    unavailable_anchor = (
        '    else:\n'
        '        performance["metrics_basis"] = truth.get("metrics_basis", "unavailable")\n'
    )

    if unavailable_anchor in api:
        unavailable_replacement = (
            '    else:\n'
            '        # Never show a stale legacy win rate when truthful metrics are unavailable.\n'
            '        performance["win_rate"] = None\n'
            '        performance["win_rate_estimate"] = None\n'
            '        performance["metrics_basis"] = truth.get("metrics_basis", "unavailable")\n'
        )
        api = api.replace(unavailable_anchor, unavailable_replacement, 1)

    api_path.write_text(api, encoding="utf-8")
    print("API_PATCH_OK: dashboard win-rate fields now follow truthful metrics")
else:
    print("API_PATCH_ALREADY_PRESENT")
