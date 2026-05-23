import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock database and other dependencies before importing main logic
mock_engine = MagicMock()
sys.modules['core.db'] = MagicMock(engine=mock_engine)
sys.modules['core.config'] = MagicMock(settings=MagicMock(symbol_list=['BTC/USDT'], live_trading=False, trade_interval_seconds=0))
sys.modules['services.metrics'] = MagicMock()
sys.modules['services.telegram'] = MagicMock()
sys.modules['core.control'] = MagicMock()
sys.modules['data.ingestion'] = MagicMock()
sys.modules['data.feature_store'] = MagicMock()
sys.modules['execution.executor'] = MagicMock()
sys.modules['ai.autonomous_agent'] = MagicMock()

import main

class TestStrategySafety(unittest.TestCase):
    def setUp(self):
        main.shadow_positions.clear()
        main.shadow_entry_prices.clear()
        main.shadow_trade_counts.clear()

    @patch('main.engine.begin')
    def test_entry_policy_blocks_blocked_strategy(self, mock_begin):
        mock_conn = MagicMock()
        mock_begin.return_value.__enter__.return_value = mock_conn
        
        # Scenario: Strategy is blocked in strategy_scores table
        mock_conn.execute.side_effect = [
            MagicMock(first=MagicMock(return_value=None)), # symbol_quarantine check (none)
            MagicMock(mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value={"status": "blocked"})))) # strategy_scores check
        ]
        
        allowed, reason = main.entry_policy_allows("BTC/USDT", "BULL", 0.9, 0, strategy="bad_strat")
        self.assertFalse(allowed)
        self.assertEqual(reason, "strategy_bad_strat_blocked")

    def test_apply_shadow_buy_logic(self):
        fill = {
            "symbol": "BTC/USDT",
            "quantity": 2.0,
            "fill_price": 40000.0,
            "strategy": "shadow_tester"
        }
        
        main.apply_shadow_buy(fill)
        self.assertEqual(main.shadow_positions["BTC/USDT"], 2.0)
        self.assertEqual(main.shadow_entry_prices["BTC/USDT"], 40000.0)
        
        # Test averaging
        fill2 = {
            "symbol": "BTC/USDT",
            "quantity": 2.0,
            "fill_price": 50000.0,
            "strategy": "shadow_tester"
        }
        main.apply_shadow_buy(fill2)
        self.assertEqual(main.shadow_positions["BTC/USDT"], 4.0)
        self.assertEqual(main.shadow_entry_prices["BTC/USDT"], 45000.0)

    def test_apply_shadow_sell_logic(self):
        main.shadow_positions["BTC/USDT"] = 1.0
        main.shadow_entry_prices["BTC/USDT"] = 50000.0
        
        sell = main.apply_shadow_sell("BTC/USDT", 0.5, 55000.0, "take_profit")
        self.assertIsNotNone(sell)
        self.assertEqual(main.shadow_positions["BTC/USDT"], 0.5)
        self.assertTrue(sell["shadow_mode"])
        
        # Sell remaining
        main.apply_shadow_sell("BTC/USDT", 0.5, 56000.0, "take_profit")
        self.assertEqual(main.shadow_positions["BTC/USDT"], 0.0)
        self.assertNotIn("BTC/USDT", main.shadow_entry_prices)

if __name__ == "__main__":
    unittest.main()
