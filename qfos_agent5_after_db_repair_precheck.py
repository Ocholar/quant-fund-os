import sqlite3, json, time
from pathlib import Path

db = Path("data/quant.db")
print("DB_PATH:", db.resolve())
print("DB_EXISTS:", db.exists())

if not db.exists():
    raise SystemExit("FAIL: host DB missing")

con = sqlite3.connect(str(db))
cur = con.cursor()

def exists(table):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None

for table in ["trades", "positions", "portfolio_snapshots", "strategy_scores"]:
    print("\nTABLE", table)
    if not exists(table):
        print("MISSING")
        continue
    print("schema:", cur.execute(f"PRAGMA table_info({table})").fetchall())
    try:
        print("rows:", cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 25").fetchall())
    except Exception as e:
        print("ERR", repr(e))

print("\nTRADE_SIDE_COUNTS:")
try:
    print(cur.execute("SELECT side, COUNT(*) FROM trades GROUP BY side ORDER BY side").fetchall())
except Exception as e:
    print("ERR", repr(e))

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
    print("ERR", repr(e))

print("\nNEGATIVE_POSITIONS:")
try:
    rows = cur.execute("""
        SELECT symbol, quantity
        FROM positions
        WHERE quantity < -0.00000001
    """).fetchall()
    print(rows if rows else "NONE")
except Exception as e:
    print("ERR", repr(e))

print("\nBASELINE_JSON:")
baseline = {}
try:
    baseline["max_trade_id"] = cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0]
except Exception:
    baseline["max_trade_id"] = 0
try:
    baseline["trade_count"] = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
except Exception:
    baseline["trade_count"] = -1
try:
    baseline["buy_count"] = cur.execute("SELECT COUNT(*) FROM trades WHERE side='buy'").fetchone()[0]
except Exception:
    baseline["buy_count"] = -1
try:
    baseline["sell_count"] = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell'").fetchone()[0]
except Exception:
    baseline["sell_count"] = -1
try:
    baseline["open_position_count"] = cur.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001").fetchone()[0]
except Exception:
    baseline["open_position_count"] = -1
try:
    baseline["snapshot_count"] = cur.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
except Exception:
    baseline["snapshot_count"] = -1

baseline["ts"] = time.time()
print(json.dumps(baseline, indent=2))

con.close()
