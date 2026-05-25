import os
import random
import ccxt


class PaperMarketData:
    def __init__(self, symbols):
        self.symbols = symbols
        self.prices = {s: self._initial_price(s) for s in symbols}

    def _initial_price(self, symbol):
        base = symbol.split("/")[0]
        defaults = {
            "BTC": 65000,
            "ETH": 3200,
            "BNB": 600,
            "SOL": 150,
            "XRP": 0.6,
            "ADA": 0.45,
            "DOGE": 0.14,
            "DOT": 7,
            "MATIC": 0.7,
            "LTC": 80,
            "LINK": 15,
            "BCH": 450,
            "TRX": 0.12,
            "ATOM": 9,
            "AVAX": 35,
            "ETC": 28,
            "AAVE": 100,
            "UNI": 8,
            "XLM": 0.1,
            "NEAR": 6,
            "VET": 0.04,
            "FTM": 0.7,
            "EOS": 0.8,
            "FIL": 6,
        }
        return defaults.get(base, 100)

    def tick(self):
        for symbol in self.symbols:
            change = random.uniform(-0.01, 0.01)
            self.prices[symbol] *= 1 + change

        return {
            "source": "simulated",
            "prices": self.prices,
        }


class RealMarketData:
    def __init__(self, symbols, exchange_name="binance"):
        self.symbols = symbols
        self.exchange_name = exchange_name
        self.exchange = getattr(ccxt, exchange_name)({
            "enableRateLimit": True,
        })

        self.fallback = PaperMarketData(symbols)
        self.last_prices = {}

    def tick(self):
        prices = {}

        try:
            tickers = self.exchange.fetch_tickers(self.symbols)

            for symbol in self.symbols:
                ticker = tickers.get(symbol)
                price = None

                if ticker:
                    price = ticker.get("last") or ticker.get("close")

                if price:
                    prices[symbol] = float(price)
                    self.last_prices[symbol] = float(price)
                elif symbol in self.last_prices:
                    prices[symbol] = self.last_prices[symbol]
                else:
                    prices[symbol] = self.fallback.prices.get(symbol, 100)

            return {
                "source": f"real:{self.exchange_name}",
                "prices": prices,
            }

        except Exception as e:
            from core.config import settings
            if settings.live_trading:
                import sys
                print(f"CRITICAL: Real market data failed in LIVE MODE! Terminating immediately to prevent simulated prices from executing live orders. Exception: {e}")
                sys.exit(1)
            
            fallback_tick = self.fallback.tick()
            fallback_tick["source"] = f"fallback_simulated_after_error:{str(e)[:120]}"
            return fallback_tick


def build_market_data(symbols):
    use_real_market_data = os.getenv("USE_REAL_MARKET_DATA", "false").lower() == "true"
    exchange_name = os.getenv("MARKET_DATA_EXCHANGE", "binance")

    if use_real_market_data:
        return RealMarketData(symbols, exchange_name)

    return PaperMarketData(symbols)
