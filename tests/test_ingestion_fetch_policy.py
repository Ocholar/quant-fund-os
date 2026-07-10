import unittest

from data.ingestion import PaperMarketData


class FailingBulkExchange:
    def __init__(self):
        self.fetch_ticker_calls = 0

    def fetch_tickers(self, symbols):
        raise RuntimeError("bulk unavailable")

    def fetch_ticker(self, symbol):
        self.fetch_ticker_calls += 1
        raise AssertionError("serial fallback must never run")


class IngestionFetchPolicyTests(unittest.TestCase):
    def test_bulk_failure_does_not_trigger_serial_symbol_calls(self):
        market = PaperMarketData(["BTC/USDT", "ETH/USDT"])
        market.exchange = FailingBulkExchange()

        prices = market._fetch_ccxt_prices()

        self.assertEqual(prices, {})
        self.assertEqual(market.exchange.fetch_ticker_calls, 0)

    def test_rest_batch_replaces_insufficient_ccxt_result(self):
        market = PaperMarketData(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        market._fetch_ccxt_prices = lambda: {"BTC/USDT": 1.0}
        market._fetch_mexc_rest_prices = lambda: {
            "BTC/USDT": 2.0,
            "ETH/USDT": 3.0,
            "SOL/USDT": 4.0,
        }

        prices = market._fetch_real_prices()

        self.assertEqual(
            prices,
            {
                "BTC/USDT": 2.0,
                "ETH/USDT": 3.0,
                "SOL/USDT": 4.0,
            },
        )

    def test_full_small_universe_ccxt_result_skips_rest_batch(self):
        market = PaperMarketData(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        market._fetch_ccxt_prices = lambda: {
            "BTC/USDT": 1.0,
            "ETH/USDT": 2.0,
            "SOL/USDT": 3.0,
        }

        def forbidden_rest():
            raise AssertionError("REST must not run when CCXT covers the full small universe")

        market._fetch_mexc_rest_prices = forbidden_rest

        prices = market._fetch_real_prices()

        self.assertEqual(len(prices), 3)

    def test_large_universe_requires_half_or_five_whichever_is_larger(self):
        symbols = [f"COIN{i}/USDT" for i in range(57)]
        market = PaperMarketData(symbols)
        market._fetch_ccxt_prices = lambda: {
            symbol: 1.0 for symbol in symbols[:27]
        }
        market._fetch_mexc_rest_prices = lambda: {
            symbol: 2.0 for symbol in symbols[:57]
        }

        prices = market._fetch_real_prices()

        self.assertEqual(len(prices), 57)
        self.assertEqual(prices[symbols[0]], 2.0)


if __name__ == "__main__":
    unittest.main()
