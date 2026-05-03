def detect_regime(volatility: float, trend_strength: float) -> str:
    if volatility > 0.045:
        return "RISK_OFF"
    if trend_strength > 0.025:
        return "TREND"
    return "SIDEWAYS"
