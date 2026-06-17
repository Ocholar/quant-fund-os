import sqlite3
import re
from pathlib import Path

source = Path("/app/main.py").read_text(encoding="utf-8-sig")
m = re.search(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    source,
    flags=re.S,
)

if not m:
    raise SystemExit("atomic block not found")

ns = {}
exec(m.group(0), ns)

conn = sqlite3.connect("/app/data/quant.db")
symbols = ns["qfos_reconcile_stale_closed_positions"](conn, source="manual_phase2g_repair")
conn.commit()

print("RECONCILED_SYMBOLS:")
print(symbols if symbols else "NONE")
