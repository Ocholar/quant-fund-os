import sqlite3
from pathlib import Path

db = Path("data") / "quant.db"
db.parent.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(str(db))
cur = con.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL,
    cash REAL,
    exposure REAL,
    drawdown REAL,
    regime TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL,
    avg_entry REAL,
    realized_pnl REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    last_price REAL,
    exposure REAL,
    strategy TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    side TEXT,
    quantity REAL,
    expected_price REAL,
    fill_price REAL,
    slippage_bps REAL DEFAULT 0,
    pnl REAL DEFAULT 0,
    strategy TEXT,
    confidence REAL,
    live BOOLEAN DEFAULT 0,
    shadow_mode BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

for col, ddl in {
    "expected_price": "REAL",
    "fill_price": "REAL",
    "slippage_bps": "REAL DEFAULT 0",
    "pnl": "REAL DEFAULT 0",
    "strategy": "TEXT",
    "confidence": "REAL",
    "live": "BOOLEAN DEFAULT 0",
    "shadow_mode": "BOOLEAN DEFAULT 0",
    "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
}.items():
    existing = {r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()}
    if col not in existing:
        cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
        print("added trades column", col)

cur.execute("DELETE FROM trades")
cur.execute("DELETE FROM positions")
cur.execute("DELETE FROM portfolio_snapshots")

for table in ["profit_engine_state", "profit_engine_peaks", "symbol_quarantine"]:
    try:
        cur.execute(f"DELETE FROM {table}")
        print("cleared", table)
    except Exception:
        pass

cur.execute("""
INSERT INTO portfolio_snapshots(equity, cash, exposure, drawdown, regime, created_at)
VALUES(100.0, 100.0, 0.0, 0.0, 'SIDEWAYS', DATETIME('now', '+3 hours'))
""")

con.commit()

print("DB:", db)
print("trades_schema:", cur.execute("PRAGMA table_info(trades)").fetchall())
print("trades_count:", cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
print("positions_count:", cur.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
print("portfolio_latest:", cur.execute("SELECT equity, cash, exposure, drawdown, regime, created_at FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone())

con.close()
print("clean paper state reset complete")
