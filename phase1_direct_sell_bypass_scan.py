from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8")

blocks = list(re.finditer(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
))

if len(blocks) != 1:
    print(f"FAIL: expected exactly 1 canonical atomic block, found {len(blocks)}")
    raise SystemExit(1)

outside = s[:blocks[0].start()] + s[blocks[0].end():]

if re.search(r"def\s+qfos_persist_fill_atomic\s*\(", outside):
    print("FAIL: duplicate qfos_persist_fill_atomic outside canonical block")
    raise SystemExit(1)

if re.search(r"def\s+_qfos_atomic_insert_trade\s*\(", outside):
    print("FAIL: legacy _qfos_atomic_insert_trade still exists outside canonical block")
    raise SystemExit(1)

danger = []
for m in re.finditer(r"INSERT\s+INTO\s+trades", outside, flags=re.I):
    start = max(0, m.start() - 600)
    end = min(len(outside), m.end() + 900)
    chunk = outside[start:end]
    lower = chunk.lower()

    if "create table" in lower and "trades" in lower:
        continue

    if any(word in lower for word in ["side", "sell", "fill", "strategy", "pnl", "quantity"]):
        danger.append(chunk)

if danger:
    print("FAIL: POTENTIAL_DIRECT_TRADES_INSERT_OUTSIDE_ATOMIC_BOUNDARY")
    for i, chunk in enumerate(danger, 1):
        print(f"\n--- POTENTIAL BYPASS {i} ---")
        print(chunk)
    raise SystemExit(1)

print("PASS: exactly one atomic boundary and no obvious direct SELL-capable trades insert outside it")
