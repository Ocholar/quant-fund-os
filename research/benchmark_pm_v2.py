import time
import os
import json
import random
import uuid
import statistics
from pathlib import Path
from sqlalchemy import create_engine, text
from core.config import settings

settings.pm_v2_enabled = True

from core.pm_v2 import (
    pm_v2_on_capacity_rejection,
    pm_v2_on_trade_closed,
    pm_v2_record_entry_score
)

DB_PATH = "test_pm_benchmark.db"

def setup_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except: pass
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE positions (symbol TEXT, quantity REAL)"))
        conn.execute(text("CREATE TABLE trades (trade_uuid TEXT, symbol TEXT, side TEXT, quantity REAL, created_at TEXT, confidence REAL)"))
        for i in range(15):
            conn.execute(text(f"INSERT INTO positions (symbol, quantity) VALUES ('SYM_{i}', 1.0)"))
    return engine

def _calc_stats(name, times_ms):
    times_ms.sort()
    mean = statistics.mean(times_ms)
    median = statistics.median(times_ms)
    p95 = times_ms[int(len(times_ms) * 0.95)]
    p99 = times_ms[int(len(times_ms) * 0.99)]
    maximum = max(times_ms)
    print(f"{name:15} | Mean: {mean:6.3f} ms | Median: {median:6.3f} ms | P95: {p95:6.3f} ms | P99: {p99:6.3f} ms | Max: {maximum:6.3f} ms")

def test_benchmark():
    engine = setup_db()
    
    with engine.begin() as conn:
        buy_times = []
        for _ in range(100):
            t0 = time.perf_counter()
            pm_v2_record_entry_score(symbol="BTC", trade_uuid=str(uuid.uuid4()), score=0.95, conn=conn)
            buy_times.append((time.perf_counter() - t0) * 1000)

        rej_times = []
        for _ in range(100):
            t0 = time.perf_counter()
            pm_v2_on_capacity_rejection(
                incoming_symbol="ETH",
                incoming_score=0.98,
                incoming_rank=1,
                rejection_reason="max_open_positions",
                engine=engine
            )
            rej_times.append((time.perf_counter() - t0) * 1000)

        sell_times = []
        for _ in range(100):
            t0 = time.perf_counter()
            pm_v2_on_trade_closed(
                symbol="BTC",
                trade_uuid=str(uuid.uuid4()),
                side="sell",
                pnl=15.5,
                engine=engine
            )
            sell_times.append((time.perf_counter() - t0) * 1000)

    print("--- Latency Benchmarks ---")
    _calc_stats("BUY Hook", buy_times)
    _calc_stats("REJECT Hook", rej_times)
    _calc_stats("SELL Hook", sell_times)
    
    engine.dispose()
    if os.path.exists(DB_PATH):
        try: os.remove(DB_PATH)
        except: pass

def test_stress():
    print("\n--- Stress Testing ---")
    engine = setup_db()
    start_time = time.time()
    
    with engine.begin() as conn:
        # 500 Buys
        for i in range(500):
            t_id = str(uuid.uuid4())
            pm_v2_record_entry_score(symbol=f"SYM_{i}", trade_uuid=t_id, score=random.uniform(0.1, 0.9), conn=conn)
        
    # 1000 capacity rejections
    for i in range(1000):
        pm_v2_on_capacity_rejection(
            incoming_symbol=f"SYM_NEW_{i}",
            incoming_score=random.uniform(0.5, 0.99),
            incoming_rank=1,
            rejection_reason="max_open_positions",
            engine=engine
        )
            
    # 500 Sells
    for i in range(500):
        pm_v2_on_trade_closed(
            symbol=f"SYM_{i}",
            trade_uuid=str(uuid.uuid4()),
            side="sell",
            pnl=random.uniform(-10, 50),
            engine=engine
        )
        
    duration = time.time() - start_time
    print(f"Stress test (2000 events) completed in {duration:.3f} seconds")
    
    # Verify DB rows
    with engine.begin() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM pm_v2_entry_scores")).scalar()
    print(f"Rows in pm_v2_entry_scores: {result}")
    
    engine.dispose()
    if os.path.exists(DB_PATH):
        try: os.remove(DB_PATH)
        except: pass
        
    # Check log sizes
    for f in ["logs/pm_v2/pm_v2_candidates.jsonl", "logs/pm_v2/pm_v2_outcomes.jsonl"]:
        if os.path.exists(f):
            print(f"Log {f} size: {os.path.getsize(f)} bytes")
            # Clear logs for clean state
            try: os.remove(f)
            except: pass

if __name__ == '__main__':
    test_benchmark()
    test_stress()
