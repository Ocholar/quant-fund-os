def crash_path(prices, crash=-0.35, at=None):
    prices = list(prices)
    at = at if at is not None else len(prices)//2
    return prices[:at] + [p * (1 + crash) for p in prices[at:]]
