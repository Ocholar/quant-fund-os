import sqlite3
from pathlib import Path

db = Path("data/quant.db")
print("DB_EXISTS:", db.exists(), db)

if not db.exists():
    raise SystemExit(0)

con = sqlite3.connect(str(db))
cur = con.cursor()

for table in ["trades", "positions", "portfolio_snapshots"]:
    print("\nTABLE", table)
    try:
        print("SCHEMA:")
        for row in cur.execute(f"PRAGMA table_info({table})").fetchall():
            print(row)
        print("RECENT ROWS:")
        for row in cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20").fetchall():
            print(row)
    except Exception as e:
        print("ERR", e)

print("\nTRADE SIDE COUNTS:")
try:
    print(cur.execute("SELECT side, COUNT(*) FROM trades GROUP BY side ORDER BY side").fetchall())
except Exception as e:
    print("ERR", e)

print("\nSELL EXIT ACCOUNTING CHECK:")
try:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
    if "is_exit" in cols or "exit_reason" in cols:
        q = """
        SELECT id, symbol, side, quantity, strategy,
               COALESCE(is_exit, 'MISSING') AS is_exit,
               COALESCE(exit_reason, 'MISSING') AS exit_reason,
               created_at
        FROM trades
        WHERE side='sell'
        ORDER BY id DESC
        LIMIT 30
        """
        for row in cur.execute(q).fetchall():
            print(row)
    else:
        print("trades table has no is_exit/exit_reason columns")
except Exception as e:
    print("ERR", e)

con.close()
