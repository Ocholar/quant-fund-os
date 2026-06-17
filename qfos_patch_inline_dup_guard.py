from pathlib import Path

p = Path("main.py")
text = p.read_text(encoding="utf-8")

marker = "QFOS_INLINE_DUP_SELL_GUARD_V1"

if marker in text:
    print("Inline duplicate sell guard already installed.")
else:
    lines = text.splitlines()
    out = []
    inserted = False

    guard = [
        "    # QFOS_INLINE_DUP_SELL_GUARD_V1",
        "    # Prevent repeated direct Profit Engine full-exit sells for the same open position.",
        "    try:",
        "        _qfos_inline_reason = str(reason or '').lower()",
        "        _qfos_inline_full_exit_reasons = {",
        "            'sideways_green_to_red_exit',",
        "            'sideways_scalp_stop_loss',",
        "            'sideways_scalp_take_profit',",
        "            'sideways_max_hold_profit_engine',",
        "            'adaptive_stop_loss',",
        "            'adaptive_take_profit',",
        "            'trailing_profit_exit',",
        "            'breakeven_protection_exit',",
        "            'time_stop_exit',",
        "            'risk_off_exit',",
        "            'emergency_exposure_reduction',",
        "            'basket_loss_guard',",
        "            'big_loss_cooldown_exit',",
        "        }",
        "        def _qfos_inline_val(obj, key, default=None):",
        "            try:",
        "                if isinstance(obj, dict):",
        "                    return obj.get(key, default)",
        "            except Exception:",
        "                pass",
        "            try:",
        "                return obj[key]",
        "            except Exception:",
        "                pass",
        "            try:",
        "                return getattr(obj, key)",
        "            except Exception:",
        "                return default",
        "        _qfos_inline_symbol = str(_qfos_inline_val(pos, 'symbol') or _qfos_inline_val(pos, 0) or '')",
        "        _qfos_inline_req_qty = float(qty or 0.0)",
        "        if _qfos_inline_symbol and _qfos_inline_reason in _qfos_inline_full_exit_reasons:",
        "            try:",
        "                _row = cur.execute('SELECT quantity FROM positions WHERE symbol = ? LIMIT 1', (_qfos_inline_symbol,)).fetchone()",
        "                _open_qty = float(_row[0] or 0.0) if _row else 0.0",
        "                if _open_qty <= 0.00000001:",
        "                    print(f'[DUP_SELL_GUARD] blocked_no_open_qty symbol={_qfos_inline_symbol} reason={_qfos_inline_reason} db_qty={_open_qty}', flush=True)",
        "                    return False",
        "                _buy = cur.execute(\"SELECT created_at FROM trades WHERE symbol = ? AND LOWER(side) = 'buy' ORDER BY datetime(created_at) DESC, id DESC LIMIT 1\", (_qfos_inline_symbol,)).fetchone()",
        "                if _buy:",
        "                    _sell = cur.execute(\"SELECT id, strategy, created_at FROM trades WHERE symbol = ? AND LOWER(side) = 'sell' AND datetime(created_at) >= datetime(?) ORDER BY datetime(created_at) DESC, id DESC LIMIT 1\", (_qfos_inline_symbol, _buy[0])).fetchone()",
        "                else:",
        "                    _sell = cur.execute(\"SELECT id, strategy, created_at FROM trades WHERE symbol = ? AND LOWER(side) = 'sell' ORDER BY datetime(created_at) DESC, id DESC LIMIT 1\", (_qfos_inline_symbol,)).fetchone()",
        "                if _sell and str(_sell[1] or '').lower() in _qfos_inline_full_exit_reasons:",
        "                    print(f'[DUP_SELL_GUARD] blocked_duplicate_full_exit symbol={_qfos_inline_symbol} reason={_qfos_inline_reason} prior={_sell[1]}', flush=True)",
        "                    return False",
        "                if _qfos_inline_req_qty > _open_qty:",
        "                    print(f'[DUP_SELL_GUARD] capped_qty symbol={_qfos_inline_symbol} reason={_qfos_inline_reason} requested={_qfos_inline_req_qty:.8f} open={_open_qty:.8f}', flush=True)",
        "                    qty = _open_qty",
        "            except Exception as _qfos_inline_exc:",
        "                print(f'[DUP_SELL_GUARD] check_error symbol={_qfos_inline_symbol} reason={_qfos_inline_reason} err={_qfos_inline_exc}', flush=True)",
        "    except Exception as _qfos_inline_outer_exc:",
        "        print(f'[DUP_SELL_GUARD] outer_error={_qfos_inline_outer_exc}', flush=True)",
    ]

    for line in lines:
        out.append(line)
        if not inserted and line.startswith("def _qfos_pe_sell("):
            out.extend(guard)
            inserted = True

    if not inserted:
        raise SystemExit("Could not find top-level def _qfos_pe_sell(")

    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("Inserted inline duplicate sell guard inside _qfos_pe_sell().")
