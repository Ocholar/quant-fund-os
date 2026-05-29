from datetime import datetime, timezone

def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _pct(entry, mark):
    try:
        entry = float(entry or 0)
        mark = float(mark or 0)
        if entry <= 0:
            return 0.0
        return (mark - entry) / entry
    except Exception:
        return 0.0

def allow_risk_off_exit(position, global_state=None, portfolio=None, reason=""):
    """
    True  = allow old risk_off_exit sell.
    False = suppress false/premature risk_off_exit.
    Conservative fallback version designed not to crash.
    """
    regime = str(_get(global_state, "regime", "") or "").upper()
    symbol = _get(position, "symbol", "UNKNOWN")
    entry_price = _get(position, "entry_price", None)
    mark_price = _get(position, "mark_price", None) or _get(position, "price", None)

    pnl_pct = _pct(entry_price, mark_price)

    # Hard emergency exits must still be allowed.
    if "HARD" in regime or "EMERGENCY" in str(reason).upper():
        return True

    # Profit shield: do not panic-sell winners during soft RISK_OFF.
    if "RISK_OFF" in regime and pnl_pct >= 0:
        try:
            with open("winning_strategy_decisions.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    '{"action":"HOLD","reason":"profit_shield_suppressed_risk_off_exit",'
                    f'"symbol":"{symbol}","pnl_pct":{pnl_pct}}}\n'
                )
        except Exception:
            pass
        return False

    # If the trade is only slightly down, avoid panic-selling unless local SL handles it.
    if "RISK_OFF" in regime and pnl_pct > -0.003:
        try:
            with open("winning_strategy_decisions.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    '{"action":"TIGHT_TRAIL","reason":"minor_loss_suppressed_risk_off_exit",'
                    f'"symbol":"{symbol}","pnl_pct":{pnl_pct}}}\n'
                )
        except Exception:
            pass
        return False

    return True

def decide_exit(*args, **kwargs):
    return {"action": "HOLD", "reason": "compatibility_default"}

def position_size_multiplier(regime):
    r = str(regime or "").upper()
    if r == "RISK_OFF":
        return 0.0
    if r in {"RISK_CAUTION", "RISK_OFF_SOFT"}:
        return 0.25
    if r == "TREND":
        return 1.2
    return 1.0
