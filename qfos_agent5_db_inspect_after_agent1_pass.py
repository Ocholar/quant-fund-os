import sqlite3, json, time
from pathlib import Path

db = Path("data/quant.db")
print("DB_PATH:", db.resolve())
print("DB_EXISTS:", db.exists())

if not db.exists():
    raise SystemExit("FAIL: host DB missing")

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
        print("ERR", repr(e))

def scalar(q, args=(), default=0):
    try:
        row = cur.execute(q, args).fetchone()
        return row[0] if row else default
    except Exception:
        return default

def rows(q, args=()):
    try:
        return cur.execute(q, args).fetchall()
    except Exception as e:
        return [("ERR", str(e))]

print("\nTRADE_SIDE_COUNTS:")
print(rows("SELECT side, COUNT(*) FROM trades GROUP BY side ORDER BY side"))

print("\nSELLS_WITH_BAD_EXIT_ACCOUNTING:")
cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
has_exit_cols = "is_exit" in cols and "exit_reason" in cols
if not has_exit_cols:
    print("FAIL_SCHEMA_MISSING_EXIT_COLUMNS")
else:
    bad = rows("""
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
    """)
    print(bad if bad else "NONE")

print("\nNEGATIVE_POSITIONS:")
neg = rows("""
    SELECT symbol, quantity
    FROM positions
    WHERE quantity < -0.00000001
""")
print(neg if neg else "NONE")

print("\nOPEN_POSITIONS:")
open_pos = rows("""
    SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
    FROM positions
    WHERE quantity > 0.00000001
    ORDER BY symbol
""")
print(open_pos if open_pos else "NONE")

print("\nBASELINE_JSON:")
baseline = {
    "max_trade_id": scalar("SELECT COALESCE(MAX(id), 0) FROM trades", default=0),
    "trade_count": scalar("SELECT COUNT(*) FROM trades", default=0),
    "buy_count": scalar("SELECT COUNT(*) FROM trades WHERE side='buy'", default=0),
    "sell_count": scalar("SELECT COUNT(*) FROM trades WHERE side='sell'", default=0),
    "open_position_count": scalar("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001", default=0),
    "negative_position_count": scalar("SELECT COUNT(*) FROM positions WHERE quantity < -0.00000001", default=0),
    "snapshot_count": scalar("SELECT COUNT(*) FROM portfolio_snapshots", default=0),
    "has_exit_cols": has_exit_cols,
    "ts": time.time(),
}
print(json.dumps(baseline, indent=2))

con.close()
