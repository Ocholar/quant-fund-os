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

required_in_block = [
    "def _qfos_cleanup_closed_symbol_runtime_state(",
    "def _qfos_latest_trade_is_sell_and_no_open_qty(",
    "reason=\"duplicate_latest_sell\"",
]

for item in required_in_block:
    if item not in block:
        print("FAIL missing in atomic block:", item)
        raise SystemExit(1)

if "def _qfos_pe_sell" in s:
    if "PHASE2D_PE_DUPLICATE_SELL_SOURCE_GUARD" not in s:
        print("FAIL: _qfos_pe_sell exists but source guard was not inserted")
        raise SystemExit(1)
    print("PASS: _qfos_pe_sell source guard exists")
else:
    print("WARN: _qfos_pe_sell not found; only atomic cleanup guard verified")

print("PASS: Phase 2D cleanup helpers and duplicate_latest_sell cleanup are present")
