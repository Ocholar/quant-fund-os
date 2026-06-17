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
    "_QFOS_CLOSED_SYMBOL_TOMBSTONES",
    "def _qfos_mark_symbol_closed(",
    "def _qfos_reject_or_reconcile_tombstoned_sell(",
    "SELL_TOMBSTONE_RECONCILED_STALE_POSITION",
    "QFOS_SYMBOL_CLOSED_TOMBSTONE_SET",
]
for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

print("PASS: Phase 2F tombstone guard is present")
