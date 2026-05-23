from sqlalchemy import text
from core.db import engine

schema = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    expected_price REAL NOT NULL,
    fill_price REAL NOT NULL,
    slippage_bps REAL NOT NULL DEFAULT 0,
    pnl REAL NOT NULL DEFAULT 0,
    strategy TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    live BOOLEAN NOT NULL DEFAULT FALSE,
    shadow_mode BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    exposure REAL NOT NULL,
    drawdown REAL NOT NULL,
    regime TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS strategy_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL UNIQUE,
    sharpe REAL NOT NULL DEFAULT 0,
    drawdown REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL NOT NULL DEFAULT 0,
    avg_entry REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    last_price REAL NOT NULL DEFAULT 0,
    exposure REAL NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS symbol_quarantine (
    symbol TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_until DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

with engine.begin() as conn:
    for statement in schema.split(';'):
        if statement.strip():
            conn.execute(text(statement))

print("Full SQLite Schema initialized.")
