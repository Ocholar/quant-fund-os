import sqlite3
import re
import os
import json

db_path = "data/quant.db"

print("--- Forensic DB Analysis ---")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM trades GROUP BY status")
    for row in cur.fetchall():
        print(f"Status: {row[0]}, Count: {row[1]}")
    conn.close()
else:
    print("No SQLite db found.")

print("\n--- Forensic Log Analysis ---")
log_file = "docker_logs.txt"
if os.path.exists(log_file):
    with open(log_file, "r", encoding="utf-16", errors="ignore") as f:
        lines = f.readlines()
        
    rejected_count = 0
    duplicate_sell_triggers = 0
    top_n_triggers = 0
    exceptions = 0
    evaluated_total = 0

    for line in lines:
        if "rejected_candidates=" in line:
            m = re.search(r"rejected_candidates=(\d+)", line)
            if m:
                rejected_count += int(m.group(1))
        if "ranked_candidates=" in line:
            m = re.search(r"ranked_candidates=(\d+)", line)
            if m:
                evaluated_total += int(m.group(1))
        if "duplicate sell" in line.lower():
            duplicate_sell_triggers += 1
        if "entry_quality_top_n" in line.lower():
            top_n_triggers += 1
        if "exception" in line.lower() and "metrics reconciliation" not in line.lower() and "dust_aware" not in line.lower():
            exceptions += 1

    print(f"Total Evaluated Candidates (approx from telemetry): {evaluated_total + rejected_count}")
    print(f"Total Rejected Candidates (approx): {rejected_count}")
    print(f"Duplicate Sell Guard Triggers: {duplicate_sell_triggers}")
    print(f"Entry Quality Rule Mentions: {top_n_triggers}")
    print(f"Unhandled Exceptions (excluding known metric warnings): {exceptions}")
