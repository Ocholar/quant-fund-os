from pathlib import Path
import re

src = Path("main.py").read_text(encoding="utf-8")

start = src.find("def qfos_persist_fill_atomic(conn, fill, source=")
if start < 0:
    raise SystemExit("ERROR: qfos_persist_fill_atomic definition not found")

next_defs = []
for marker in (
    "\ndef ",
    "\nclass ",
    "\n# END ",
    "\nif __name__",
):
    pos = src.find(marker, start + 20)
    if pos > start:
        next_defs.append(pos)

end = min(next_defs) if next_defs else min(len(src), start + 40000)
block = src[start:end]

Path("atomic_persistence_function.txt").write_text(block, encoding="utf-8")

for needle in (
    "sideways_take_profit_exit",
    "sideways_stagnation_exit",
    "return False",
    "return None",
    "FILL_PERSISTED_ATOMIC",
    "EXIT_SELL_BLOCK",
    "latest_buy",
    "take_profit",
    "QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT",
):
    print(f"=== MATCHES: {needle} ===")
    for m in re.finditer(re.escape(needle), block):
        a = max(0, m.start() - 700)
        b = min(len(block), m.end() + 1000)
        print(block[a:b])
        print("\n---\n")
