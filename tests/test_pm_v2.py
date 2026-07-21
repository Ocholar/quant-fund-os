import pytest
import time
from sqlalchemy import create_engine, text
from core.pm_v2 import _pm_v2_observe
from core.config import settings

@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE positions (symbol TEXT, quantity REAL)
        """))
        conn.execute(text("""
            CREATE TABLE trades (trade_uuid TEXT, symbol TEXT, side TEXT, quantity REAL, confidence REAL, created_at TEXT)
        """))
        conn.execute(text("""
            CREATE TABLE pm_v2_entry_scores (trade_uuid TEXT, symbol TEXT, score REAL, created_at TEXT)
        """))
    return engine

def test_pm_v2_replacement_logic(mock_db, tmp_path):
    # Enable PM_V2
    settings.pm_v2_enabled = True
    settings.pm_v2_dry_run = True
    
    # Override log directory to avoid side-effects
    from core import pm_v2
    pm_v2._CAND_LOG = tmp_path / "pm_v2_candidates.jsonl"
    
    # Portfolio: 
    # A score 0.012, age 18m
    # B score 0.009, age 40m
    
    from datetime import datetime, timezone
    now_ts = time.time()
    a_time = datetime.fromtimestamp(now_ts - (18 * 60), timezone.utc).isoformat()
    b_time = datetime.fromtimestamp(now_ts - (40 * 60), timezone.utc).isoformat()
    
    with mock_db.begin() as conn:
        conn.execute(text("INSERT INTO positions (symbol, quantity) VALUES ('SYM_A', 1.0)"))
        conn.execute(text("INSERT INTO positions (symbol, quantity) VALUES ('SYM_B', 1.0)"))
        
        # A entry_score = 0.012
        conn.execute(text(f"INSERT INTO trades (trade_uuid, symbol, side, quantity, created_at) VALUES ('uuidA', 'SYM_A', 'buy', 1.0, '{a_time}')"))
        conn.execute(text(f"INSERT INTO pm_v2_entry_scores (trade_uuid, symbol, score, created_at) VALUES ('uuidA', 'SYM_A', 0.012, '{a_time}')"))
        
        # B entry_score = 0.009
        conn.execute(text(f"INSERT INTO trades (trade_uuid, symbol, side, quantity, created_at) VALUES ('uuidB', 'SYM_B', 'buy', 1.0, '{b_time}')"))
        conn.execute(text(f"INSERT INTO pm_v2_entry_scores (trade_uuid, symbol, score, created_at) VALUES ('uuidB', 'SYM_B', 0.009, '{b_time}')"))

    # C score 0.015 incoming rank1
    _pm_v2_observe(
        incoming_symbol="SYM_C",
        incoming_score=0.015,
        incoming_rank=1,
        rejection_reason="max_open_positions",
        engine=mock_db
    )
    
    # Expect replace B (score 0.009 vs 0.015 is delta 0.006 > 0.001)
    import json
    lines = pm_v2._CAND_LOG.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    
    assert record["decision"] == "REPLACE"
    assert record["incumbent_symbol"] == "SYM_B"
    assert record["candidate_symbol"] == "SYM_C"
    
    # Then age = 10m. Expect NO replacement.
    a2_time = datetime.fromtimestamp(now_ts - (10 * 60), timezone.utc).isoformat()
    b2_time = datetime.fromtimestamp(now_ts - (10 * 60), timezone.utc).isoformat()
    with mock_db.begin() as conn:
        conn.execute(text("DELETE FROM pm_v2_entry_scores"))
        conn.execute(text("DELETE FROM trades"))
        conn.execute(text(f"INSERT INTO trades (trade_uuid, symbol, side, quantity, created_at) VALUES ('uuidA2', 'SYM_A', 'buy', 1.0, '{a2_time}')"))
        conn.execute(text(f"INSERT INTO pm_v2_entry_scores (trade_uuid, symbol, score, created_at) VALUES ('uuidA2', 'SYM_A', 0.012, '{a2_time}')"))
        conn.execute(text(f"INSERT INTO trades (trade_uuid, symbol, side, quantity, created_at) VALUES ('uuidB2', 'SYM_B', 'buy', 1.0, '{b2_time}')"))
        conn.execute(text(f"INSERT INTO pm_v2_entry_scores (trade_uuid, symbol, score, created_at) VALUES ('uuidB2', 'SYM_B', 0.009, '{b2_time}')"))
    
    _pm_v2_observe(
        incoming_symbol="SYM_C",
        incoming_score=0.015,
        incoming_rank=1,
        rejection_reason="max_open_positions",
        engine=mock_db
    )
    
    lines = pm_v2._CAND_LOG.read_text().strip().split("\n")
    record2 = json.loads(lines[1])
    
    assert record2["decision"] == "KEEP"
    assert record2["incumbent_symbol"] is None
