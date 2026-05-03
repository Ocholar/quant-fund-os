class OrderBook:
    def __init__(self, bids, asks):
        self.bids = sorted(bids, key=lambda x: x[0], reverse=True)
        self.asks = sorted(asks, key=lambda x: x[0])

    @staticmethod
    def synthetic(mid: float, depth=10, spread_bps=6):
        spread = mid * spread_bps / 10_000
        bids, asks = [], []
        for i in range(depth):
            bids.append((mid - spread/2 - i * spread, 1 + i * 0.4))
            asks.append((mid + spread/2 + i * spread, 1 + i * 0.4))
        return OrderBook(bids, asks)

    def market_buy(self, qty):
        return self._consume(self.asks, qty)

    def market_sell(self, qty):
        return self._consume(self.bids, qty)

    def _consume(self, levels, qty):
        remaining, value = qty, 0.0
        for price, size in levels:
            fill = min(remaining, size)
            value += fill * price
            remaining -= fill
            if remaining <= 1e-12:
                break
        if remaining > 1e-12:
            raise ValueError("insufficient synthetic liquidity")
        return value / qty
