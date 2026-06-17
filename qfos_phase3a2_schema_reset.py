import sqlite3
import re
from pathlib import Path

source = Path("/app/main.py").read_text(encoding="utf-8-sig")

# Run startup guard manually too, so we know it executed.
m = re.search(
    r"# BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1.*?# END QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1",
    source,
    flags=re.S,
)
if not m:
    raise SystemExit("schema guard block not found")

ns = {}
exec(m.group(0), ns)
ns["qfos_ensure_execution_accounting_schema_and_guards"]("/app/data/quant.db", source="manual_phase3a2")

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("TRADES_SCHEMA:")
for row in cur.execute("PRAGMA table_info(trades)").fetchall():
    print(row)

print("\nTRIGGERS:")
for row in cur.execute("""
SELECT name, tbl_name
FROM sqlite_master
WHERE type='trigger'
  AND name LIKE 'qfos_block_stale_position_%'
ORDER BY name
""").fetchall():
    print(row)

# Clean reset AFTER schema/triggers exist.
for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        cur.execute(f"DELETE FROM {table}")
        print("CLEARED", table)
    except Exception as e:
        print("CLEAR_SKIP", table, e)

conn.commit()

print("\nPOST_RESET_COUNTS:")
for table in ["trades", "positions", "portfolio_snapshots"]:
    print(table, cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

conn.close()
