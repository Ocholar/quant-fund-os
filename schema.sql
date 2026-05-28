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
    live INTEGER NOT NULL DEFAULT 0,
    shadow_mode INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    exposure REAL NOT NULL,
    drawdown REAL NOT NULL,
    regime TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    sharpe REAL NOT NULL DEFAULT 0,
    drawdown REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL NOT NULL DEFAULT 0,
    avg_entry REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    last_price REAL NOT NULL DEFAULT 0,
    exposure REAL NOT NULL DEFAULT 0,
    strategy TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbol_quarantine (
    symbol TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_quarantine (
    strategy TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_created_at
ON trades(symbol, created_at);

CREATE INDEX IF NOT EXISTS idx_trades_strategy_created_at
ON trades(strategy, created_at);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_created_at
ON portfolio_snapshots(created_at);
