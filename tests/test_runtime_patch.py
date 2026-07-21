import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../")

# 1. Test FeatureStore Import
def test_feature_store_import():
    try:
        from data.feature_store import FeatureStore
        assert True
    except ImportError as e:
        assert False, f"Failed to import from data.feature_store: {e}"

# 2. Test Allocator State Adapter
def test_allocator_state_adapter():
    with patch("sqlalchemy.create_engine", MagicMock()):
        import main
        
        raw_state = {
            "cash": 100.0,
            "equity": 150.0,
            "exposure": 50.0,
            "buy_cost": 50.0, 
            "sell_proceeds": 0.0, 
            "risk_status": "SAFE" 
        }
        
        adapted = main._qfos_adapt_alloc_state(raw_state)
        assert "cash" in adapted
        assert "equity" in adapted
        assert "buy_cost" not in adapted
        assert "sell_proceeds" not in adapted
        assert "risk_status" not in adapted

# 3. Test Ledger Caching
def test_ledger_caching():
    with patch("sqlalchemy.create_engine", MagicMock()):
        import main
        
        # Initial state
        main.qfos_stop_evaluation_batch()
        
        # Mock compute to trace calls
        call_count = 0
        def mock_compute():
            nonlocal call_count
            call_count += 1
            return {"cash": 100.0}
        
        original_compute = main._compute_qfos_active_canbuy_ledger_state
        main._compute_qfos_active_canbuy_ledger_state = mock_compute
        
        try:
            # Outside batch -> always fresh
            main.qfos_active_canbuy_ledger_state()
            main.qfos_active_canbuy_ledger_state()
            assert call_count == 2
            
            # Start batch
            call_count = 0
            main.qfos_start_evaluation_batch()
            
            main.qfos_active_canbuy_ledger_state()
            main.qfos_active_canbuy_ledger_state()
            assert call_count == 1 # Second call should be cached
            
            # Invalidate (simulate a trade)
            main.qfos_invalidate_ledger_cache()
            main.qfos_active_canbuy_ledger_state()
            assert call_count == 2 # Should compute fresh
            
            # Stop batch
            main.qfos_stop_evaluation_batch()
            main.qfos_active_canbuy_ledger_state()
            assert call_count == 3 # Should compute fresh
            
        finally:
            main._compute_qfos_active_canbuy_ledger_state = original_compute
            
if __name__ == "__main__":
    test_feature_store_import()
    test_allocator_state_adapter()
    test_ledger_caching()
    print("All regression tests passed!")
