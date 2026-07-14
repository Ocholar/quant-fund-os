from collections import defaultdict, deque
import numpy as np
import os


class FeatureStore:
    """Rolling feature calculator for entry filtering.

    Global regime is market weather.
    Symbol regime is each coin's own behavior.

    This store computes per-symbol trend quality so the bot can detect
    individual coin breakouts even when the broad market is SIDEWAYS
    or RISK_OFF.
    """

    def __init__(self, window=120):
        self.window = max(60, int(window))
        self.warmup_ticks = int(os.getenv('FEATURE_WARMUP_TICKS', '60'))
        self.history = defaultdict(lambda: deque(maxlen=window))

    def update(self, prices: dict[str, float]):
        for sym, price in prices.items():
            try:
                p = float(price)
            except Exception:
                continue
            if p > 0:
                self.history[sym].append(p)

    def _symbol_regime(
        self,
        trend: float,
        long_trend: float,
        momentum: float,
        one_tick_momentum: float,
        volatility: float,
        signal_strength: float,
    ) -> tuple[str, float, float, float]:
        """
        Per-symbol regime classification.

        This does not replace global market regime.
        It tells us whether one coin is locally tradable.
        """

        trend_score = (
            max(0.0, trend) * 1.00
            + max(0.0, momentum) * 0.90
            + max(0.0, long_trend) * 0.50
            + max(0.0, one_tick_momentum) * 0.25
        )

        down_score = (
            max(0.0, -trend) * 1.00
            + max(0.0, -momentum) * 0.90
            + max(0.0, -long_trend) * 0.50
            + max(0.0, -one_tick_momentum) * 0.25
        )

        # Breakout rewards strong short + medium movement but penalizes extreme noise.
        volatility_penalty = min(0.02, max(0.0, volatility) * 0.25)
        breakout_score = max(0.0, trend_score - volatility_penalty)

        # Quality is positive only when movement is aligned enough.
        aligned_up = trend > 0 and momentum > 0 and one_tick_momentum >= -0.0015
        aligned_down = trend < 0 and momentum < 0 and one_tick_momentum <= 0.0015

        if aligned_up and signal_strength >= 0.006:
            regime = "SYMBOL_BREAKOUT_UP"
        elif aligned_up and signal_strength >= 0.001:
            regime = "SYMBOL_TREND_UP"
        elif aligned_down and down_score >= 0.001:
            regime = "SYMBOL_TREND_DOWN"
        elif volatility >= 0.03 and abs(momentum) < 0.001:
            regime = "SYMBOL_CHOPPY"
        else:
            regime = "SYMBOL_NEUTRAL"

        # Trend quality is what the allocator can use for ranking.
        trend_quality = trend_score if regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP") else 0.0

        return regime, float(trend_score), float(breakout_score), float(trend_quality)

    def features(self, symbol: str) -> dict:
        arr = np.array(self.history[symbol], dtype=float)

        if len(arr) < self.warmup_ticks:
            return {
                "ready": False,
                "symbol_regime": "WARMING_UP",
                "symbol_trend_score": 0.0,
                "breakout_score": 0.0,
                "trend_quality": 0.0,
                "is_symbol_uptrend": False,
                "is_symbol_downtrend": False,
                "is_choppy": False,
            }

        returns = np.diff(arr) / arr[:-1]
        price = float(arr[-1])

        short_ma = float(arr[-5:].mean())
        medium_ma = float(arr[-20:].mean())
        long_ma = float(arr[-60:].mean())

        trend = (short_ma / medium_ma) - 1 if medium_ma else 0.0
        long_trend = (medium_ma / long_ma) - 1 if long_ma else 0.0
        momentum = (arr[-1] / arr[-4]) - 1 if len(arr) >= 4 and arr[-4] else 0.0
        one_tick_momentum = (arr[-1] / arr[-2]) - 1 if len(arr) >= 2 and arr[-2] else 0.0
        volatility = float(returns[-20:].std()) if len(returns) >= 20 else float(returns.std())

        signal_strength = (
            max(0.0, trend)
            + max(0.0, momentum)
            + max(0.0, long_trend * 0.5)
        )

        symbol_regime, symbol_trend_score, breakout_score, trend_quality = self._symbol_regime(
            trend,
            long_trend,
            momentum,
            one_tick_momentum,
            volatility,
            signal_strength,
        )

        return {
            "ready": True,
            "source": "NORMAL",
            "trend": float(trend),
            "long_trend": float(long_trend),
            "volatility": float(volatility),
            "momentum": float(momentum),
            "one_tick_momentum": float(one_tick_momentum),
            "signal_strength": float(signal_strength),
            "price": price,

            # Per-symbol regime fields
            "symbol_regime": symbol_regime,
            "symbol_trend_score": float(symbol_trend_score),
            "breakout_score": float(breakout_score),
            "trend_quality": float(trend_quality),
            "is_symbol_uptrend": symbol_regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"),
            "is_symbol_downtrend": symbol_regime == "SYMBOL_TREND_DOWN",
            "is_choppy": symbol_regime == "SYMBOL_CHOPPY",
        }
