from collections import defaultdict, deque
import numpy as np

class FeatureStore:
    def __init__(self, window=120):
        self.window = window
        self.history = defaultdict(lambda: deque(maxlen=window))

    def update(self, prices: dict[str, float]):
        for sym, price in prices.items():
            self.history[sym].append(float(price))

    def features(self, symbol: str) -> dict:
        arr = np.array(self.history[symbol], dtype=float)
        if len(arr) < 3:
            return {"ready": False}
        returns = np.diff(arr) / arr[:-1]
        short = arr[-2:].mean()
        long = arr[-3:].mean()
        trend = (short / long) - 1
        vol = returns[-3:].std()
        momentum = (arr[-1] / arr[-2]) - 1
        return {"ready": True, "trend": float(trend), "volatility": float(vol), "momentum": float(momentum), "price": float(arr[-1])}
