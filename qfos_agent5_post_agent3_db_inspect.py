import sqlite3, json, time
from pathlib import Path

db = Path("data/quant.db")
print("DB_PATH:", db.resolve())
print("DB_EXISTS:", db.exists())

if not db.exists():
    raise SystemExit("DB missing")

con = sqlite3.connect(str(db))
cur = con.cursor()

def table_exists(name):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone() is not None

for table in ["trades", "positions", "portfolio_snapshots", "strategy_scores"]:
    print("\nTABLE", table)
    if not table_exists(table):
        print("MISSING")
        continue
    print("schema:", cur.execute(f"PRAGMA table_info({table})").fetchall())
    try:
        print("rows:", cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 25").fetchall())
    except Exception as e:
        print("ERR rows", e)

print("\nTRADE_SIDE_COUNTS:")
try:
    print(cur.execute("SELECT side, COUNT(*) FROM trades GROUP BY side ORDER BY side").fetchall())
except Exception as e:
    print("ERR", e)

print("\nNEGATIVE_POSITIONS:")
try:
    print(cur.execute("SELECT symbol, quantity FROM positions WHERE quantity < -0.00000001").fetchall())
except Exception as e:
    print("ERR", e)

print("\nSELLS_WITH_BAD_EXIT_ACCOUNTING:")
try:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
    if "is_exit" not in cols or "exit_reason" not in cols:
        print("FAIL_SCHEMA_MISSING_EXIT_COLUMNS")
    else:
        rows = cur.execute("""
            SELECT id, symbol, side, quantity, strategy, is_exit, exit_reason, created_at
            FROM trades
            WHERE side='sell'
              AND (
                COALESCE(is_exit, 0)=0
                OR exit_reason IS NULL
                OR TRIM(exit_reason)=''
              )
            ORDER BY id DESC
            LIMIT 25
        """).fetchall()
        print(rows if rows else "NONE")
except Exception as e:
    print("ERR", e)

print("\nBASELINE:")
try:
    max_trade_id = cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0]
except Exception:
    max_trade_id = 0

try:
    pos_count = cur.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001").fetchone()[0]
except Exception:
    pos_count = -1

try:
    snap_count = cur.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
except Exception:
    snap_count = -1

print(json.dumps({
    "max_trade_id": max_trade_id,
    "open_position_count": pos_count,
    "portfolio_snapshot_count": snap_count,
    "ts": time.time()
}, indent=2))

con.close()
