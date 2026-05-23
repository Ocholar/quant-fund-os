from unittest.mock import MagicMock, patch
import sys
from sqlalchemy import text

# Mock database engine to avoid real connections during import
mock_engine = MagicMock()
with patch('core.db.engine', mock_engine), patch('core.db.engine.begin', mock_engine.begin):
    from main import entry_policy_allows, apply_shadow_buy, shadow_positions, shadow_entry_prices

def test_entry_policy_blocks_quarantined_symbol():
    mock_conn = MagicMock()
    # Mock symbol_quarantine check
    mock_conn.execute.return_value.first.return_value = ("BTC/USDT",)
    
    with patch('core.db.engine.begin') as mock_begin:
        mock_begin.return_value.__enter__.return_value = mock_conn
        allowed, reason = entry_policy_allows("BTC/USDT", "BULL", 0.9, 0)
        assert allowed is False
        assert reason == "symbol_quarantined"

def test_entry_policy_blocks_blocked_strategy():
    mock_conn = MagicMock()
    # Mock symbol_quarantine (none) and strategy_scores (blocked)
    mock_conn.execute.side_effect = [
        MagicMock(first=MagicMock(return_value=None)), # symbol_quarantine
        MagicMock(mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value={"status": "blocked"})))) # strategy_scores
    ]
    
    with patch('core.db.engine.begin') as mock_begin:
        mock_begin.return_value.__enter__.return_value = mock_conn
        allowed, reason = entry_policy_allows("ETH/USDT", "BULL", 0.9, 0, strategy="los_str")
        assert allowed is False
        assert "blocked" in reason

def test_apply_shadow_buy():
    fill = {
        "symbol": "BTC/USDT",
        "quantity": 1.0,
        "fill_price": 50000.0,
        "strategy": "test_strat"
    }
    
    shadow_positions.clear()
    shadow_entry_prices.clear()
    
    success = apply_shadow_buy(fill)
    assert success is True
    assert shadow_positions["BTC/USDT"] == 1.0
    assert shadow_entry_prices["BTC/USDT"] == 50000.0
    
    # Test averaging
    fill2 = {
        "symbol": "BTC/USDT",
        "quantity": 1.0,
        "fill_price": 60000.0,
        "strategy": "test_strat"
    }
    apply_shadow_buy(fill2)
    assert shadow_positions["BTC/USDT"] == 2.0
    assert shadow_entry_prices["BTC/USDT"] == 55000.0

if __name__ == "__main__":
    pytest.main([__file__])
