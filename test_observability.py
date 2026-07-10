import os
import glob
import json
import unittest
from observability import events, _manager, RejectionReason

class TestObservabilityCore(unittest.TestCase):
    def setUp(self):
        # Clear test logs before run
        for f in glob.glob("logs/candidates/*.jsonl") + glob.glob("logs/trades/*.jsonl"):
            try:
                os.remove(f)
            except Exception:
                pass

    def test_synthetic_event_lifecycle_and_schema(self):
        # 1. Candidate Ranked
        cid = events.candidate_ranked(
            cycle_id=100,
            rank=1,
            symbol="BTC/USDT",
            strength=0.035,
            momentum=0.002,
            volatility=0.01,
            confidence=0.035,
            regime="SIDEWAYS",
            source="NORMAL",
            score_before_filters=0.85
        )
        self.assertTrue(cid)

        # 2. Candidate Filtered
        events.candidate_filtered(
            candidate_id=cid,
            cycle_id=100,
            symbol="BTC/USDT",
            rank=1,
            reason=RejectionReason.QUARANTINE,
            filter_name="quarantine_check",
            filter_stage=2,
            details={"threshold_hours": 4, "actual_hours": 2}
        )

        # 3. Candidate Approved & Trade Executed
        cid2 = events.candidate_ranked(
            cycle_id=100,
            rank=2,
            symbol="ETH/USDT",
            strength=0.030,
            momentum=0.001,
            volatility=0.012,
            confidence=0.030,
            regime="SIDEWAYS",
            source="NORMAL",
            score_before_filters=0.80
        )
        events.candidate_approved(candidate_id=cid2, cycle_id=100, symbol="ETH/USDT", rank=2)
        
        tid = events.trade_executed(
            candidate_id=cid2,
            cycle_id=100,
            symbol="ETH/USDT",
            entry_price=3000.0,
            position_size=1.5,
            cash_available=10000.0,
            current_exposure=4500.0,
            open_positions=1
        )
        self.assertTrue(tid)
        
        events.trade_open(trade_id=tid, candidate_id=cid2, symbol="ETH/USDT")
        events.trade_exited(
            trade_id=tid,
            candidate_id=cid2,
            symbol="ETH/USDT",
            exit_price=3050.0,
            holding_time_seconds=600,
            realized_pnl=75.0,
            exit_reason="TAKE_PROFIT"
        )
        events.cycle_summary(
            cycle_id=100,
            total_candidates=2,
            candidates_above_threshold=2,
            filtered_count=1,
            approved_count=1,
            executed_count=1,
            regime="SIDEWAYS",
            evaluation_time_ms=45.2
        )

        _manager.close()

        # Verify Candidates log file
        cand_files = glob.glob("logs/candidates/*.jsonl")
        self.assertTrue(len(cand_files) > 0)
        with open(cand_files[0], "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
            self.assertTrue(len(lines) >= 4) # candidate_ranked x2, candidate_filtered, candidate_approved, cycle_summary
            for rec in lines:
                self.assertEqual(rec["schema_version"], "1.0")
                self.assertIn("timestamp", rec)
                self.assertIn("metadata", rec)

        # Verify Trades log file
        trade_files = glob.glob("logs/trades/*.jsonl")
        self.assertTrue(len(trade_files) > 0)
        with open(trade_files[0], "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
            self.assertEqual(len(lines), 3) # trade_executed, trade_open, trade_exited
            for rec in lines:
                self.assertEqual(rec["schema_version"], "1.0")

if __name__ == "__main__":
    unittest.main()
