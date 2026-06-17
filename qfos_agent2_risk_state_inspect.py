import sqlite3
from pathlib import Path

db_path = Path("data/quant.db")

print("DB_PATH", db_path.resolve())
print("DB_EXISTS", db_path.exists())

if not db_path.exists():
    raise SystemExit(0)

con = sqlite3.connect(str(db_path))
cur = con.cursor()

for table in ["portfolio_snapshots", "trades", "positions", "symbol_quarantine", "strategy_scores"]:
    print("\nTABLE", table)
    try:
        print("SCHEMA")
        for row in cur.execute(f"PRAGMA table_info({table})").fetchall():
            print(row)

        print("LATEST_ROWS")
        for row in cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20").fetchall():
            print(row)

    except Exception as e:
        print("ERR", e)

con.close()
