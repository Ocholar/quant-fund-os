CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
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
    is_exit INTEGER NOT NULL DEFAULT 0,
    exit_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    exposure REAL NOT NULL,
    drawdown REAL NOT NULL,
    regime TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_scores (
    id SERIAL PRIMARY KEY,
    strategy TEXT NOT NULL,
    sharpe REAL NOT NULL DEFAULT 0,
    drawdown REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbol_quarantine (
    symbol TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_until TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_quarantine (
    strategy TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_until TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profit_engine_state (
    id SERIAL PRIMARY KEY,
    symbol TEXT,
    state_data TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profit_engine_peaks (
    id SERIAL PRIMARY KEY,
    symbol TEXT,
    peak_price REAL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS db_probe (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_created_at ON trades(symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_created_at ON trades(strategy, created_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_created_at ON portfolio_snapshots(created_at);
CREATE TABLE IF NOT EXISTS symbol_feature_snapshot (
    symbol TEXT PRIMARY KEY,
    price DOUBLE PRECISION,
    trend DOUBLE PRECISION,
    long_trend DOUBLE PRECISION,
    volatility DOUBLE PRECISION,
    momentum DOUBLE PRECISION,
    one_tick_momentum DOUBLE PRECISION,
    signal_strength DOUBLE PRECISION,
    symbol_regime TEXT,
    symbol_trend_score DOUBLE PRECISION,
    breakout_score DOUBLE PRECISION,
    trend_quality DOUBLE PRECISION,
    is_symbol_uptrend BOOLEAN NOT NULL DEFAULT FALSE,
    is_symbol_downtrend BOOLEAN NOT NULL DEFAULT FALSE,
    is_choppy BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_symbol_feature_snapshot_updated_at
ON symbol_feature_snapshot(updated_at);