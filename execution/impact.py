def square_root_impact(order_size: float, daily_volume: float, volatility: float) -> float:
    if daily_volume <= 0:
        return volatility
    participation = max(order_size / daily_volume, 0)
    return volatility * participation ** 0.5
