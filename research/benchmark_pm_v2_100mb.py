import os
import time
import json

def test_100mb_append():
    log_file = "logs/pm_v2/pm_v2_100mb_test.jsonl"
    os.makedirs("logs/pm_v2", exist_ok=True)
    
    if os.path.exists(log_file):
        os.remove(log_file)
        
    print("Generating ~100MB of JSONL logs...")
    start_time = time.time()
    
    dummy_data = {
        "timestamp": "2026-07-17T12:00:00Z",
        "action": "PM_V2_CAPACITY_REJECT",
        "evicted_symbol": "BTC",
        "evicted_entry_score": 0.85,
        "evicted_age_minutes": 45.0,
        "incoming_symbol": "ETH",
        "incoming_score": 0.99,
        "rejection_reason": "max_open_positions",
        "replacement_would_fire": True,
        "open_positions_count": 10,
        "eligible_incumbents": 5
    }
    
    json_line = json.dumps(dummy_data) + "\n"
    target_size = 100 * 1024 * 1024 # 100MB
    
    # 1. Fill to 100MB
    with open(log_file, "a", encoding="utf-8") as f:
        while f.tell() < target_size:
            f.write(json_line)
            
    print(f"Reached 100MB in {time.time() - start_time:.2f} seconds.")
    
    # 2. Benchmark single append at 100MB
    t0 = time.perf_counter()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json_line)
    t1 = time.perf_counter()
    
    print(f"Single append latency at 100MB file size: {(t1 - t0) * 1000:.3f} ms")
    
    # 3. Simulate external log rotation (move file, create new)
    os.rename(log_file, log_file + ".1")
    
    t0 = time.perf_counter()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json_line)
    t1 = time.perf_counter()
    
    print(f"Single append latency immediately after rotation: {(t1 - t0) * 1000:.3f} ms")
    
    # Cleanup
    os.remove(log_file)
    os.remove(log_file + ".1")

if __name__ == "__main__":
    test_100mb_append()
