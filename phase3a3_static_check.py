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
    "def _qfos_symbol_buy_lifecycle_qty(",
    "def _qfos_has_valid_buy_lifecycle_for_sell(",
    "def qfos_reconcile_positions_without_buy_lifecycle(",
    "no_buy_lifecycle_position_zeroed",
    "QFOS_NO_BUY_LIFECYCLE_POSITION_ZEROED",
]

for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

if "qfos_reconcile_positions_without_buy_lifecycle" not in s:
    print("FAIL: no-BUY reconciler not referenced")
    raise SystemExit(1)

print("PASS: Phase 3A3 BUY-lifecycle guard exists")
