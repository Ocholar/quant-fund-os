"""
Quant Fund OS market ingestion.

Design rules:
- Do not invent prices.
- Do not random-walk missing prices.
- Do not generate synthetic ~$100 fallback prices.
- Prefer real MEXC/CCXT spot prices.
- If live fetch fails, return fresh last-known-good prices only.
- tick() returns {"prices": {...}} because main.py expects tick["prices"].
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from typing import Dict, Iterable, Optional, Any


MIN_VALID_PRICE = 1e-8
MAX_STALE_SECONDS = 900
MEXC_REST_TICKERS_URL = "https://api.mexc.com/api/v3/ticker/24hr"

STABLE_NEAR_ONE = {"USDC/USDT", "USD1/USDT"}
NEAR_ONE_FX = {"EUR/USDT"}

KNOWN_EXCLUDED_MICRO = {
    "MOGU/USDT",
    "SHIB/USDT",
    "AIXDROP/USDT",
}

LARGE_PRICE_ALLOWED = {
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "BCH/USDT",
    "ZEC/USDT",
    "XMR/USDT",
    "TAO/USDT",
    "LTC/USDT",
    "SOL/USDT",
    "HYPE/USDT",
    "ATLA/USDT",
    "ULTIMA/USDT",
    "GOLD(PAXG)/USDT",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _now() -> float:
    return time.time()


def _norm_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _mexc_symbol_id(symbol: str) -> str:
    s = _norm_symbol(symbol)
    return s.replace("/", "").replace("(PAXG)", "PAXG")


def _is_synthetic_like(symbol: str, price: float) -> bool:
    s = _norm_symbol(symbol)
    p = _safe_float(price)

    if p <= 0:
        return True

    if s in STABLE_NEAR_ONE and not (0.95 <= p <= 1.05):
        return True

    if s in NEAR_ONE_FX and not (0.80 <= p <= 1.40):
        return True

    # Known fake-feed pattern: many small/mid symbols suddenly print near $100.
    if s not in LARGE_PRICE_ALLOWED and 95 <= p <= 105:
        return True

    return False


def _jump_ok(symbol: str, price: float, last_price: Optional[float]) -> bool:
    if not last_price or last_price <= 0:
        return True

    p = _safe_float(price)
    ratio = p / float(last_price)

    s = _norm_symbol(symbol)

    if s in STABLE_NEAR_ONE:
        return 0.98 <= ratio <= 1.02

    if s in NEAR_ONE_FX:
        return 0.80 <= ratio <= 1.25

    # Do not allow instant 3x / 70% collapse into the data feed.
    return 0.333 <= ratio <= 3.0


class PaperMarketData:
    """
    Real-price-only market provider.

    Compatibility:
    - main.py calls market.tick()["prices"]
    - Some older code may call latest_prices(), get_prices(), or snapshot()
    """

    def __init__(self, symbols: Iterable[str]):
        self.symbols = [_norm_symbol(s) for s in symbols if _norm_symbol(s)]
        self.last_good: Dict[str, float] = {}
        self.last_seen_ts: Dict[str, float] = {}
        self.exchange = None
        self._ccxt_unavailable_logged = False
        self._init_exchange()

    def _init_exchange(self) -> None:
        try:
            import ccxt  # type: ignore

            self.exchange = ccxt.mexc({
                "enableRateLimit": True,
                "timeout": 15000,
                "options": {"defaultType": "spot"},
            })
        except Exception as e:
            self.exchange = None
            if not self._ccxt_unavailable_logged:
                print(f"MARKET DATA WARNING: ccxt unavailable: {e}")
                self._ccxt_unavailable_logged = True

    def _fetch_ccxt_prices(self) -> Dict[str, float]:
        if self.exchange is None:
            self._init_exchange()

        if self.exchange is None:
            return {}

        prices: Dict[str, float] = {}

        try:
            tickers = self.exchange.fetch_tickers(self.symbols)
            for symbol in self.symbols:
                t = tickers.get(symbol) or {}
                px = (
                    t.get("last")
                    or t.get("close")
                    or t.get("bid")
                    or t.get("ask")
                )
                px = _safe_float(px)
                if px > 0:
                    prices[symbol] = px
            return prices
        except Exception as e:
            print(f"MARKET DATA WARNING: ccxt fetch_tickers failed: {e}")

        # Individual fallback. Slower, but real.
        for symbol in self.symbols:
            try:
                t = self.exchange.fetch_ticker(symbol)
                px = (
                    t.get("last")
                    or t.get("close")
                    or t.get("bid")
                    or t.get("ask")
                )
                px = _safe_float(px)
                if px > 0:
                    prices[symbol] = px
                time.sleep(0.04)
            except Exception as e:
                print(f"MARKET DATA WARNING: ccxt ticker failed {symbol}: {e}")

        return prices

    def _fetch_mexc_rest_prices(self) -> Dict[str, float]:
        """
        Stdlib fallback if ccxt is unavailable or fails.
        Uses public MEXC ticker endpoint and maps BTCUSDT -> BTC/USDT.
        """
        wanted = {_mexc_symbol_id(s): s for s in self.symbols}
        prices: Dict[str, float] = {}

        try:
            req = urllib.request.Request(
                MEXC_REST_TICKERS_URL,
                headers={
                    "User-Agent": "QuantFundOS/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))

            if isinstance(data, dict):
                data = [data]

            if not isinstance(data, list):
                print("MARKET DATA WARNING: MEXC REST returned non-list payload")
                return {}

            for item in data:
                if not isinstance(item, dict):
                    continue

                raw_symbol = str(item.get("symbol") or "").upper()
                symbol = wanted.get(raw_symbol)
                if not symbol:
                    continue

                px = (
                    item.get("lastPrice")
                    or item.get("last")
                    or item.get("close")
                    or item.get("price")
                )
                px = _safe_float(px)
                if px > 0:
                    prices[symbol] = px

        except Exception as e:
            print(f"MARKET DATA WARNING: MEXC REST fetch failed: {e}")

        return prices

    def _fetch_real_prices(self) -> Dict[str, float]:
        prices = self._fetch_ccxt_prices()

        # If ccxt gives too few symbols, try REST.
        if len(prices) < max(5, int(len(self.symbols) * 0.50)):
            rest_prices = self._fetch_mexc_rest_prices()
            if len(rest_prices) > len(prices):
                prices = rest_prices

        return prices

    def _fresh_last_good(self) -> Dict[str, float]:
        now = _now()
        fresh = {}

        for symbol, price in self.last_good.items():
            ts = float(self.last_seen_ts.get(symbol, 0.0) or 0.0)
            if now - ts <= MAX_STALE_SECONDS:
                fresh[symbol] = price

        if not fresh:
            print("MARKET DATA BLOCK: no_real_or_fresh_last_good_prices")

        return fresh

    def _clean_prices(self, raw: Dict[str, float]) -> Dict[str, float]:
        if not isinstance(raw, dict):
            print("MARKET DATA BLOCK: raw_prices_not_dict")
            return self._fresh_last_good()

        now = _now()
        clean: Dict[str, float] = {}
        rejected = 0

        for symbol in self.symbols:
            if symbol not in raw:
                continue

            price = _safe_float(raw.get(symbol))

            if price < MIN_VALID_PRICE:
                rejected += 1
                print(f"MARKET DATA REJECT invalid {symbol}: {raw.get(symbol)}")
                continue

            if _is_synthetic_like(symbol, price):
                rejected += 1
                print(f"MARKET DATA REJECT synthetic_like {symbol}: {price}")
                continue

            last = self.last_good.get(symbol)
            if not _jump_ok(symbol, price, last):
                rejected += 1
                print(f"MARKET DATA REJECT jump {symbol}: price={price} last={last}")
                continue

            self.last_good[symbol] = price
            self.last_seen_ts[symbol] = now
            clean[symbol] = price

        total_raw = max(len(raw), 1)
        if rejected / total_raw > 0.50:
            print(
                f"MARKET DATA BLOCK: too_many_bad_prices "
                f"rejected={rejected} total={total_raw}; using last_good only"
            )
            return self._fresh_last_good()

        if clean:
            return clean

        return self._fresh_last_good()

    def latest_prices(self) -> Dict[str, float]:
        raw = self._fetch_real_prices()
        clean = self._clean_prices(raw)
        return clean

    def get_prices(self) -> Dict[str, float]:
        return self.latest_prices()

    def snapshot(self) -> Dict[str, float]:
        return self.latest_prices()

    def tick(self) -> Dict[str, Any]:
        """
        main.py expects:
            tick = market.tick()
            raw_prices = tick["prices"]

        Return only real/clean prices under the prices key.
        """
        prices = self.latest_prices()
        return {
            "prices": prices,
            "timestamp": _now(),
            "source": "mexc_real_prices_only",
            "count": len(prices),
        }


def build_market_data(symbols):
    return PaperMarketData(symbols)
