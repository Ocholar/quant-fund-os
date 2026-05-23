from sqlalchemy import text
from core.db import engine
with engine.begin() as conn:
    conn.execute(text("CREATE TABLE IF NOT EXISTS strategy_scores (strategy TEXT PRIMARY KEY, sharpe REAL, drawdown REAL, score REAL, status TEXT)"))
    conn.execute(text("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, expected_price REAL, fill_price REAL, slippage_bps REAL, pnl REAL, strategy TEXT, confidence REAL, live BOOLEAN, shadow_mode BOOLEAN, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"))
    conn.execute(text("CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, quantity REAL, avg_entry REAL, realized_pnl REAL, unrealized_pnl REAL, last_price REAL, exposure REAL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)"))
print("SQLite Schema initialized.")
