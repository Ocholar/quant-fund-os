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
    "def _qfos_reconcile_position_from_duplicate_latest_sell(",
    "SELL_POSITION_RECONCILED_FROM_LATEST_SELL",
    "reconciled = _qfos_reconcile_position_from_duplicate_latest_sell(",
]

for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

print("PASS: Phase 2E reconciliation branch exists")
