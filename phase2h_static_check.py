from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8-sig")

required = [
    "BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1",
    "def qfos_run_stale_position_reconciler_once(",
    "def qfos_start_stale_position_reconciler_daemon(",
    "qfos_start_stale_position_reconciler_daemon(interval_seconds=10)",
    "QFOS_AUTO_STALE_RECONCILER_STARTED",
]

for item in required:
    if item not in s:
        print("FAIL missing:", item)
        raise SystemExit(1)

if s.count("BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1") != 1:
    print("FAIL: duplicate daemon blocks found")
    raise SystemExit(1)

print("PASS: Phase 2H auto reconciler daemon exists exactly once")
