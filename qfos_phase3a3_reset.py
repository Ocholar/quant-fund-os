import sqlite3
import re
from pathlib import Path

source = Path("/app/main.py").read_text(encoding="utf-8-sig")

# Ensure schema guard exists/runs.
m = re.search(
    r"# BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1.*?# END QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1",
    source,
    flags=re.S,
)
if m:
    ns = {}
    exec(m.group(0), ns)
    ns["qfos_ensure_execution_accounting_schema_and_guards"]("/app/data/quant.db", source="manual_phase3a3")

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        cur.execute(f"DELETE FROM {table}")
        print("CLEARED", table)
    except Exception as e:
        print("CLEAR_SKIP", table, e)

conn.commit()

print("POST_RESET_COUNTS:")
for table in ["trades", "positions", "portfolio_snapshots"]:
    print(table, cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

print("TRADES_SCHEMA_HAS_EXIT_COLUMNS:")
cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
print("is_exit" in cols and "exit_reason" in cols)

conn.close()
