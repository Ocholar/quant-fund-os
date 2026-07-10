import unittest

from qfos_runtime.exit_intents import deduplicate_exit_intents, from_fill


class ExitIntentTests(unittest.TestCase):
    def test_valid_sell_becomes_exit_intent(self):
        intent = from_fill(
            {
                "symbol": "PLAY/USDT",
                "side": "sell",
                "quantity": 42.7,
                "fill_price": 0.034,
                "exit_reason": "stop_loss_exit",
                "source": "qfos_exit_lifecycle",
            }
        )

        self.assertIsNotNone(intent)
        self.assertEqual(intent.symbol, "PLAY/USDT")
        self.assertEqual(intent.side, "sell")
        self.assertEqual(intent.reason, "stop_loss_exit")

    def test_invalid_fill_is_not_an_exit_intent(self):
        self.assertIsNone(
            from_fill(
                {
                    "symbol": "PLAY/USDT",
                    "side": "buy",
                    "quantity": 1,
                    "fill_price": 0.034,
                }
            )
        )

    def test_duplicate_symbol_sell_is_rejected_once(self):
        fills = [
            {
                "symbol": "TIA/USDT",
                "side": "sell",
                "quantity": 4.0,
                "fill_price": 0.38,
                "exit_reason": "take_profit_exit",
                "source": "qfos_exit_lifecycle",
            },
            {
                "symbol": "TIA/USDT",
                "side": "sell",
                "quantity": 4.0,
                "fill_price": 0.38,
                "exit_reason": "adaptive_take_profit",
                "source": "main_loop",
            },
        ]

        accepted, rejected = deduplicate_exit_intents(fills)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "duplicate_exit_intent")

    def test_distinct_symbols_survive(self):
        fills = [
            {
                "symbol": "LTC/USDT",
                "side": "sell",
                "quantity": 0.03,
                "fill_price": 45.0,
                "exit_reason": "take_profit_exit",
            },
            {
                "symbol": "XMR/USDT",
                "side": "sell",
                "quantity": 0.004,
                "fill_price": 323.0,
                "exit_reason": "max_hold_exit",
            },
        ]

        accepted, rejected = deduplicate_exit_intents(fills)

        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(rejected), 0)


if __name__ == "__main__":
    unittest.main()
