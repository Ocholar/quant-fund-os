def slippage_bps(expected_price: float, fill_price: float, side: str = "buy") -> float:
    raw = (fill_price - expected_price) / expected_price * 10_000
    return raw if side == "buy" else -raw
