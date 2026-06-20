from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8-sig")

marker = "QFOS_QUALITY_REJECTION_DETAIL_V1"
if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

needle = """        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            rejected_preview.append({'symbol': symbol, 'reason': f'entry_quality_{reason}'})
            continue
"""

replacement = """        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            # QFOS_QUALITY_REJECTION_DETAIL_V1
            try:
                _qrd = data if isinstance(data, dict) else {}
                print(
                    "[QUALITY_REJECT_DETAIL] "
                    f"symbol={symbol} "
                    f"reason={reason} "
                    f"signal={_feature_value(_qrd, 'signal_strength'):.6f} "
                    f"trend={_feature_value(_qrd, 'trend'):.6f} "
                    f"long_trend={_feature_value(_qrd, 'long_trend'):.6f} "
                    f"momentum={_feature_value(_qrd, 'momentum'):.6f} "
                    f"one_tick={_feature_value(_qrd, 'one_tick_momentum'):.6f} "
                    f"volatility={abs(_feature_value(_qrd, 'volatility')):.6f} "
                    f"symbol_regime={str(_qrd.get('symbol_regime') or '')} "
                    f"ready={bool(_qrd.get('ready'))}",
                    flush=True,
                )
            except Exception as _qrd_error:
                print(f"[QUALITY_REJECT_DETAIL] symbol={symbol} telemetry_error={_qrd_error!r}", flush=True)

            rejected_preview.append({'symbol': symbol, 'reason': f'entry_quality_{reason}'})
            continue
"""

if needle not in src:
    raise SystemExit("PATCH_FAILURE: expected entry_quality_ranked_symbols block not found.")

path.write_text(src.replace(needle, replacement, 1), encoding="utf-8")
print("PATCH_WRITE_OK")
