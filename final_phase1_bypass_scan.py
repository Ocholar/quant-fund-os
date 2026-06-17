from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8-sig")

blocks = list(re.finditer(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
))

if len(blocks) != 1:
    print(f"FAIL: expected exactly 1 atomic block, found {len(blocks)}")
    raise SystemExit(1)

outside = s[:blocks[0].start()] + s[blocks[0].end():]

bad_defs = [
    "qfos_persist_fill_atomic",
    "_qfos_atomic_insert_trade",
    "_qfos_insert_trade_atomic",
]

for name in bad_defs:
    if re.search(rf"(?m)^def\s+{name}\s*\(", outside):
        print(f"FAIL: duplicate function outside atomic block: {name}")
        raise SystemExit(1)

danger = []
for m in re.finditer(r"INSERT\s+INTO\s+trades", outside, flags=re.I):
    start = max(0, m.start() - 500)
    end = min(len(outside), m.end() + 800)
    chunk = outside[start:end]
    lower = chunk.lower()

    if "create table" in lower and "trades" in lower:
        continue

    if any(w in lower for w in ["side", "sell", "fill", "strategy", "pnl", "quantity"]):
        danger.append(chunk)

if danger:
    print("FAIL: direct trades insert outside atomic boundary still exists")
    for i, d in enumerate(danger, 1):
        print(f"\n--- HIT {i} ---")
        print(d)
    raise SystemExit(1)

print("PASS: exactly one atomic boundary and no direct SELL-capable trade insert outside it")
