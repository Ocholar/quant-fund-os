from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8-sig")
m = re.search(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
)

if not m:
    raise SystemExit("FAIL: atomic block missing")

block = m.group(0)

required = [
    "def qfos_reconcile_stale_closed_positions(",
    "QFOS_DB_STALE_POSITION_RECONCILED",
    "db_stale_closed_position_reconciled",
]

for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

print("PASS: Phase 2G DB stale closed-position reconciler exists")
