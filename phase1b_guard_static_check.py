from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8-sig")

blocks = list(re.finditer(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
))

if len(blocks) != 1:
    print(f"FAIL: expected exactly one atomic block, found {len(blocks)}")
    raise SystemExit(1)

block = blocks[0].group(0)

required = [
    "def _qfos_duplicate_sell_guard(",
    "def _qfos_latest_trade_for_symbol(",
    "dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)",
    "duplicate_latest_sell",
]

for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

outside = s[:blocks[0].start()] + s[blocks[0].end():]

if re.search(r"def\s+qfos_persist_fill_atomic\s*\(", outside):
    print("FAIL duplicate qfos_persist_fill_atomic outside atomic block")
    raise SystemExit(1)

if re.search(r"def\s+_qfos_atomic_insert_trade\s*\(", outside):
    print("FAIL legacy _qfos_atomic_insert_trade outside atomic block")
    raise SystemExit(1)

print("PASS: duplicate SELL guard exists inside the single atomic boundary")
