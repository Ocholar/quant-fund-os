from collections import defaultdict, deque
import numpy as np


class FeatureStore:
    """Rolling feature calculator for entry filtering.

    The old version used only the last 2-3 ticks, which made almost every tiny
    bounce look tradable. This version still reacts fast, but requires a short
    history and separates noisy one-tick momentum from cleaner multi-tick trend.
    """

    def __init__(self, window=120):
        self.window = window
        self.history = defaultdict(lambda: deque(maxlen=window))

    def update(self, prices: dict[str, float]):
        for sym, price in prices.items():
            self.history[sym].append(float(price))

    def features(self, symbol: str) -> dict:
        arr = np.array(self.history[symbol], dtype=float)
        if len(arr) < 20:
            return {"ready": False}

        returns = np.diff(arr) / arr[:-1]
        price = float(arr[-1])

        short_ma = float(arr[-5:].mean())
        medium_ma = float(arr[-20:].mean())
        long_ma = float(arr[-60:].mean()) if len(arr) >= 60 else medium_ma

        trend = (short_ma / medium_ma) - 1 if medium_ma else 0.0
        long_trend = (medium_ma / long_ma) - 1 if long_ma else 0.0
        momentum = (arr[-1] / arr[-4]) - 1 if arr[-4] else 0.0
        one_tick_momentum = (arr[-1] / arr[-2]) - 1 if arr[-2] else 0.0
        volatility = float(returns[-20:].std()) if len(returns) >= 20 else float(returns.std())

        # Positive only when both trend and momentum agree. This is an entry
        # quality measure, not a guarantee of profit.
        signal_strength = max(0.0, trend) + max(0.0, momentum) + max(0.0, long_trend * 0.5)

        return {
            "ready": True,
            "trend": float(trend),
            "long_trend": float(long_trend),
            "volatility": float(volatility),
            "momentum": float(momentum),
            "one_tick_momentum": float(one_tick_momentum),
            "signal_strength": float(signal_strength),
            "price": price,
        }
