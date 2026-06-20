from __future__ import annotations

from collections import Counter, defaultdict, deque
import json
import math
import os
import time
from typing import Any

import numpy as np


DEFAULT_MIN_HISTORY = int(os.getenv("QFOS_FEATURE_MIN_HISTORY", "8"))
DEFAULT_WINDOW = int(os.getenv("QFOS_FEATURE_WINDOW", "120"))
HEALTH_LOG_EVERY = int(os.getenv("QFOS_FEATURE_HEALTH_LOG_EVERY", "5"))

STATE_PATH = os.getenv(
    "QFOS_FEATURE_HISTORY_PATH",
    os.path.join("data", "feature_history_runtime.json"),
)


REQUIRED_READY_FIELDS = (
    "price",
    "trend",
    "long_trend",
    "volatility",
    "momentum",
    "one_tick_momentum",
    "signal_strength",
    "confidence",
    "symbol_regime",
    "breakout_score",
    "trend_quality",
    "is_symbol_uptrend",
    "is_choppy",
    "source",
    "ready",
)


def _norm_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return float(default)


def _now() -> float:
    return time.time()


class FeatureStore:
    """Rolling NORMAL feature calculator for real market prices.

    Agent 4 contract:
    - Only real market prices can enter history.
    - update() accepts either raw {symbol: price} or tick-shaped {"prices": {...}}.
    - source=NORMAL only comes from FeatureStore real-price calculations.
    - ready=True only after real price history is sufficient.
    - RAW_MOMENTUM_FALLBACK is never generated or made executable here.
    """

    def __init__(self, window: int = DEFAULT_WINDOW, min_history: int = DEFAULT_MIN_HISTORY):
        self.window = max(8, int(window))
        self.min_history = max(4, int(min_history))
        self.history = defaultdict(lambda: deque(maxlen=self.window))

        self.update_cycles = 0
        self.last_market_symbols_count = 0
        self.last_trusted_prices_count = 0
        self.last_rejected_update_count = 0
        self.last_rejection_reason_counts: Counter[str] = Counter()
        self.last_update_health: dict[str, Any] = {}
        self.state_loaded = False
        self.state_path = STATE_PATH

        self._load_state()

    def _extract_prices(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("prices")
        if isinstance(nested, dict):
            return nested
        return payload

    def _load_state(self) -> None:
        try:
            if not self.state_path or not os.path.exists(self.state_path):
                return

            with open(self.state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            histories = payload.get("history", {})
            if not isinstance(histories, dict):
                return

            loaded = 0
            for raw_symbol, values in histories.items():
                symbol = _norm_symbol(raw_symbol)
                if not symbol or not isinstance(values, list):
                    continue

                cleaned = []
                for raw_price in values[-self.window:]:
                    price = _safe_float(raw_price, 0.0)
                    if price > 0:
                        cleaned.append(float(price))

                if cleaned:
                    self.history[symbol].extend(cleaned)
                    loaded += 1

            self.state_loaded = loaded > 0
            if loaded:
                print(f"[FEATURE_STORE] loaded_history_symbols={loaded} path={self.state_path}", flush=True)

        except Exception as exc:
            print(f"[FEATURE_STORE] load_history_error={repr(exc)}", flush=True)

    def _save_state(self) -> None:
        try:
            dirname = os.path.dirname(self.state_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

            payload = {
                "saved_at": _now(),
                "window": self.window,
                "min_history": self.min_history,
                "history": {
                    symbol: list(buf)[-self.window:]
                    for symbol, buf in self.history.items()
                    if len(buf) > 0
                },
            }

            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp, self.state_path)

        except Exception as exc:
            print(f"[FEATURE_STORE] save_history_error={repr(exc)}", flush=True)

    def update(self, prices: dict[str, float]) -> dict[str, Any]:
        self.update_cycles += 1
        rejection_counts: Counter[str] = Counter()

        clean_prices = self._extract_prices(prices)
        if not isinstance(clean_prices, dict):
            rejection_counts["prices_payload_not_dict"] += 1
            clean_prices = {}

        self.last_market_symbols_count = len(clean_prices)
        trusted = 0

        for raw_symbol, raw_price in clean_prices.items():
            symbol = _norm_symbol(raw_symbol)

            if not symbol:
                rejection_counts["empty_symbol"] += 1
                continue

            if symbol in {"PRICES", "TIMESTAMP", "SOURCE", "COUNT"}:
                rejection_counts["metadata_key"] += 1
                continue

            price = _safe_float(raw_price, 0.0)

            if price <= 0:
                rejection_counts["non_positive_or_invalid_price"] += 1
                continue

            self.history[symbol].append(float(price))
            trusted += 1

        self.last_trusted_prices_count = int(trusted)
        self.last_rejected_update_count = int(sum(rejection_counts.values()))
        self.last_rejection_reason_counts = rejection_counts

        if trusted > 0:
            self._save_state()

        self.last_update_health = self.health_snapshot()

        if self.update_cycles <= 3 or self.update_cycles % HEALTH_LOG_EVERY == 0:
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
            "volatility": 0.0,
            "momentum": 0.0,
            "one_tick_momentum": 0.0,
            "signal_strength": 0.0,
            "confidence": 0.0,
            "symbol_regime": "WARMING_UP",
            "breakout_score": 0.0,
            "trend_quality": 0.0,
            "symbol_trend_score": 0.0,
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
        arr = arr[np.isfinite(arr)]
        arr = arr[arr > 0]

        if len(arr) == 0:
            return self._warming_feature(symbol, 0, 0.0)

        price = float(arr[-1])

        if len(arr) < self.min_history:
            return self._warming_feature(symbol, len(arr), price)

        returns = np.diff(arr) / arr[:-1] if len(arr) >= 2 else np.array([0.0], dtype=float)

        short_n = min(5, len(arr))
        medium_n = min(20, len(arr))
        long_n = min(60, len(arr))

        short_ma = float(arr[-short_n:].mean())
        medium_ma = float(arr[-medium_n:].mean())
        long_ma = float(arr[-long_n:].mean())

        trend = (short_ma / medium_ma) - 1 if medium_ma else 0.0
        long_trend = (medium_ma / long_ma) - 1 if long_ma else 0.0

        lookback = min(4, len(arr) - 1)
        if lookback >= 1 and arr[-1 - lookback] > 0:
            momentum = (arr[-1] / arr[-1 - lookback]) - 1
        else:
            momentum = 0.0

        one_tick_momentum = (arr[-1] / arr[-2]) - 1 if len(arr) >= 2 and arr[-2] > 0 else 0.0

        if len(returns) >= 2:
            vol_n = min(20, len(returns))
            volatility = float(returns[-vol_n:].std())
        else:
            volatility = 0.0

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

        # Numeric feature confidence required by Agent 4 contract.
        # This is metadata for readiness/ranking compatibility, not a forced trade signal.
        confidence = min(1.0, max(0.0, (signal_strength + trend_quality + breakout_score) / 0.018))

        out = {
            "ready": True,
            "source": "NORMAL",
            "rejection_reason": "",
            "history_len": int(len(arr)),
            "missing_history": 0,
            "price": float(price),
            "trend": float(trend),
            "long_trend": float(long_trend),
            "volatility": float(volatility),
            "momentum": float(momentum),
            "one_tick_momentum": float(one_tick_momentum),
            "signal_strength": float(signal_strength),
            "confidence": float(confidence),
            "symbol_regime": symbol_regime,
            "breakout_score": float(breakout_score),
            "trend_quality": float(trend_quality),
            "symbol_trend_score": float(symbol_trend_score),
            "is_symbol_uptrend": bool(symbol_regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP")),
            "is_symbol_downtrend": bool(symbol_regime == "SYMBOL_TREND_DOWN"),
            "is_choppy": bool(symbol_regime == "SYMBOL_CHOPPY"),
        }

        # Hard contract self-check: if this ever fails, do not silently emit broken ready features.
        for field in REQUIRED_READY_FIELDS:
            if field not in out:
                out["ready"] = False
                out["rejection_reason"] = f"missing_required_field_{field}"
                break

        return out

    def all_features(self, symbols=None) -> dict[str, dict[str, Any]]:
        selected = [_norm_symbol(s) for s in symbols] if symbols is not None else list(self.history.keys())
        return {symbol: self.features(symbol) for symbol in selected if symbol}

    def ready_features(self, symbols=None) -> dict[str, dict[str, Any]]:
        feats = self.all_features(symbols)
        return {
            symbol: feature
            for symbol, feature in feats.items()
            if self.is_ready_normal_feature(feature)
        }

    def is_ready_normal_feature(self, feature: Any) -> bool:
        if not isinstance(feature, dict):
            return False
        if feature.get("ready") is not True:
            return False
        if str(feature.get("source", "")).upper() != "NORMAL":
            return False
        if _safe_float(feature.get("price"), 0.0) <= 0:
            return False
        for field in REQUIRED_READY_FIELDS:
            if field not in feature:
                return False
        for field in (
            "trend",
            "long_trend",
            "volatility",
            "momentum",
            "one_tick_momentum",
            "signal_strength",
            "confidence",
            "breakout_score",
            "trend_quality",
        ):
            value = _safe_float(feature.get(field), float("nan"))
            if not math.isfinite(value):
                return False
        return True

    def health_snapshot(self) -> dict[str, Any]:
        history_symbols = sum(1 for buf in self.history.values() if len(buf) > 0)
        ready_normal = 0
        warming = 0
        contract_reject = 0
        rejection_counts: Counter[str] = Counter(self.last_rejection_reason_counts)

        for symbol, buf in self.history.items():
            history_len = len(buf)
            if history_len >= self.min_history:
                feature = self.features(symbol)
                if self.is_ready_normal_feature(feature):
                    ready_normal += 1
                else:
                    contract_reject += 1
                    reason = str(feature.get("rejection_reason") or "contract_reject")
                    rejection_counts[reason] += 1
            elif history_len > 0:
                warming += 1
                rejection_counts["insufficient_history"] += 1

        return {
            "market_symbols_count": int(self.last_market_symbols_count),
            "trusted_prices_count": int(self.last_trusted_prices_count),
            "feature_history_symbols_count": int(history_symbols),
            "normal_feature_count": int(ready_normal),
            "ready_feature_count": int(ready_normal),
            "warming_feature_count": int(warming),
            "contract_reject_count": int(contract_reject),
            "rejected_feature_count": int(sum(rejection_counts.values())),
            "rejection_reason_counts": dict(rejection_counts),
            "min_history": int(self.min_history),
            "window": int(self.window),
            "update_cycles": int(self.update_cycles),
            "state_loaded": bool(self.state_loaded),
            "state_path": self.state_path,
        }

    def log_health(self) -> None:
        h = self.health_snapshot()
        print(
            "[FEATURE_STORE] "
            f"market={h['market_symbols_count']} "
            f"trusted={h['trusted_prices_count']} "
            f"history_symbols={h['feature_history_symbols_count']} "
            f"normal={h['normal_feature_count']} "
            f"ready={h['ready_feature_count']} "
            f"warming={h['warming_feature_count']} "
            f"contract_reject={h['contract_reject_count']} "
            f"rejected={h['rejected_feature_count']} "
            f"min_history={h['min_history']} "
            f"state_loaded={h['state_loaded']} "
            f"reasons={h['rejection_reason_counts']}",
            flush=True,
        )
