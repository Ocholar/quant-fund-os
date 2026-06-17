from __future__ import annotations

from collections import Counter, defaultdict, deque
import math
from typing import Any

import numpy as np


MIN_HISTORY = 20
HEALTH_LOG_EVERY = 1


def _norm_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


class FeatureStore:
    """Rolling NORMAL feature calculator for entry filtering.

    Contract:
    - Input may be either {symbol: price} or a market tick object containing {"prices": {...}}.
    - Tradable feature source is always NORMAL.
    - RAW_MOMENTUM_FALLBACK is not implemented here and must remain diagnostic-only elsewhere.
    - ready=True means the symbol has enough real price history to produce normal technical features.
    """

    def __init__(self, window: int = 120, min_history: int = MIN_HISTORY):
        self.window = int(window)
        self.min_history = int(min_history)
        self.history = defaultdict(lambda: deque(maxlen=self.window))
        self.update_cycles = 0
        self.last_market_symbols_count = 0
        self.last_trusted_prices_count = 0
        self.last_rejected_update_count = 0
        self.last_rejection_reason_counts: Counter[str] = Counter()
        self.last_update_health: dict[str, Any] = {}

    def _extract_prices(self, payload: Any) -> dict[str, Any]:
        """Accept both raw price maps and tick objects from PaperMarketData.tick()."""
        if not isinstance(payload, dict):
            self.last_rejection_reason_counts["prices_payload_not_dict"] += 1
            return {}

        nested = payload.get("prices")
        if isinstance(nested, dict):
            return nested

        return payload

    def update(self, prices: dict[str, float]) -> dict[str, Any]:
        """Update rolling price history and return a feature-health snapshot.

        This method is intentionally tolerant of receiving the full market tick object.
        That prevents the common failure mode where market tick count is positive but
        feature histories remain empty because the caller passed {"prices": {...}}.
        """
        self.update_cycles += 1
        rejection_counts: Counter[str] = Counter()
        clean_prices = self._extract_prices(prices)

        self.last_market_symbols_count = len(clean_prices) if isinstance(clean_prices, dict) else 0
        trusted = 0

        if not isinstance(clean_prices, dict):
            rejection_counts["prices_payload_not_dict"] += 1
            clean_prices = {}

        for sym, price in clean_prices.items():
            symbol = _norm_symbol(sym)
            if not symbol:
                rejection_counts["empty_symbol"] += 1
                continue

            # Ignore common metadata keys if an old caller passes the whole tick object.
            if symbol in {"TIMESTAMP", "SOURCE", "COUNT"}:
                rejection_counts["metadata_key"] += 1
                continue

            p = _safe_float(price)
            if p is None:
                rejection_counts["non_numeric_price"] += 1
                continue

            if p <= 0:
                rejection_counts["non_positive_price"] += 1
                continue

            self.history[symbol].append(float(p))
            trusted += 1

        self.last_trusted_prices_count = trusted
        self.last_rejected_update_count = int(sum(rejection_counts.values()))
        self.last_rejection_reason_counts = rejection_counts
        self.last_update_health = self.health_snapshot()

        if self.update_cycles % HEALTH_LOG_EVERY == 0:
            self.log_health()

        return dict(self.last_update_health)

    def _symbol_regime(
        self,
        trend: float,
        long_trend: float,
        momentum: float,
        one_tick_momentum: float,
        volatility: float,
        signal_strength: float,
    ) -> tuple[str, float, float, float]:
        """Classify each symbol's local regime without replacing global market regime."""
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

        volatility_penalty = min(0.02, max(0.0, volatility) * 0.25)
        breakout_score = max(0.0, trend_score - volatility_penalty)

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

        trend_quality = trend_score if regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP") else 0.0
        return regime, float(trend_score), float(breakout_score), float(trend_quality)

    def _warming_feature(self, symbol: str, history_len: int, price: float = 0.0) -> dict[str, Any]:
        missing = max(0, self.min_history - int(history_len))
        return {
            "ready": False,
            "source": "NORMAL",
            "rejection_reason": "insufficient_history" if history_len > 0 else "no_history",
            "history_len": int(history_len),
            "missing_history": int(missing),
            "price": float(price) if price > 0 else 0.0,
            "trend": 0.0,
            "long_trend": 0.0,
            "momentum": 0.0,
            "one_tick_momentum": 0.0,
            "volatility": 0.0,
            "signal_strength": 0.0,
            "confidence": 0.0,
            "symbol_regime": "WARMING_UP",
            "symbol_trend_score": 0.0,
            "breakout_score": 0.0,
            "trend_quality": 0.0,
            "is_symbol_uptrend": False,
            "is_symbol_downtrend": False,
            "is_choppy": False,
        }

    def features(self, symbol: str) -> dict[str, Any]:
        symbol = _norm_symbol(symbol)
        buf = self.history.get(symbol)
        if not buf:
            return self._warming_feature(symbol, 0, 0.0)

        arr = np.array(buf, dtype=float)
        if len(arr) == 0:
            return self._warming_feature(symbol, 0, 0.0)

        price = float(arr[-1])
        if len(arr) < self.min_history:
            return self._warming_feature(symbol, len(arr), price)

        returns = np.diff(arr) / arr[:-1]

        short_ma = float(arr[-5:].mean())
        medium_ma = float(arr[-20:].mean())
        long_ma = float(arr[-60:].mean()) if len(arr) >= 60 else medium_ma

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

        # Confidence is feature quality/readiness, not a forced trading signal.
        confidence = min(1.0, max(0.0, (signal_strength + trend_quality) / 0.012))

        return {
            "ready": True,
            "source": "NORMAL",
            "rejection_reason": "",
            "history_len": int(len(arr)),
            "missing_history": 0,
            "trend": float(trend),
            "long_trend": float(long_trend),
            "volatility": float(volatility),
            "momentum": float(momentum),
            "one_tick_momentum": float(one_tick_momentum),
            "signal_strength": float(signal_strength),
            "confidence": float(confidence),
            "price": price,
            "symbol_regime": symbol_regime,
            "symbol_trend_score": float(symbol_trend_score),
            "breakout_score": float(breakout_score),
            "trend_quality": float(trend_quality),
            "is_symbol_uptrend": symbol_regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"),
            "is_symbol_downtrend": symbol_regime == "SYMBOL_TREND_DOWN",
            "is_choppy": symbol_regime == "SYMBOL_CHOPPY",
        }

    def all_features(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
        selected = [_norm_symbol(s) for s in symbols] if symbols is not None else list(self.history.keys())
        return {symbol: self.features(symbol) for symbol in selected if symbol}

    def ready_features(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
        feats = self.all_features(symbols)
        return {
            symbol: feature
            for symbol, feature in feats.items()
            if feature.get("ready") is True and feature.get("source") == "NORMAL"
        }

    def health_snapshot(self) -> dict[str, Any]:
        history_symbols = sum(1 for buf in self.history.values() if len(buf) > 0)
        normal = 0
        ready = 0
        rejection_counts: Counter[str] = Counter(self.last_rejection_reason_counts)

        for symbol, buf in self.history.items():
            history_len = len(buf)
            if history_len >= self.min_history:
                normal += 1
                feature = self.features(symbol)
                if feature.get("ready") is True and feature.get("source") == "NORMAL":
                    ready += 1
            elif history_len > 0:
                rejection_counts["insufficient_history"] += 1
            else:
                rejection_counts["no_history"] += 1

        return {
            "market_symbols_count": int(self.last_market_symbols_count),
            "trusted_prices_count": int(self.last_trusted_prices_count),
            "feature_history_symbols_count": int(history_symbols),
            "normal_feature_count": int(normal),
            "ready_feature_count": int(ready),
            "rejected_feature_count": int(sum(rejection_counts.values())),
            "rejection_reason_counts": dict(rejection_counts),
            "min_history": int(self.min_history),
            "window": int(self.window),
            "update_cycles": int(self.update_cycles),
        }

    def log_health(self) -> None:
        h = self.health_snapshot()
        print(
            "[FEATURE_HEALTH] "
            f"market={h['market_symbols_count']} "
            f"trusted={h['trusted_prices_count']} "
            f"history_symbols={h['feature_history_symbols_count']} "
            f"normal={h['normal_feature_count']} "
            f"ready={h['ready_feature_count']} "
            f"rejected={h['rejected_feature_count']} "
            f"reasons={h['rejection_reason_counts']}"
        )
