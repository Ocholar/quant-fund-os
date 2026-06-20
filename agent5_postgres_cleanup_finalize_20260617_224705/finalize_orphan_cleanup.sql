BEGIN;

CREATE TABLE IF NOT EXISTS orphan_position_archive (
    id SERIAL PRIMARY KEY,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT,
    quantity DOUBLE PRECISION,
    avg_entry DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    last_price DOUBLE PRECISION,
    exposure DOUBLE PRECISION,
    strategy TEXT,
    reason TEXT,
    source_table TEXT
);

-- Archive any currently positive orphan paper_position_sync positions first.
INSERT INTO orphan_position_archive (
    symbol,
    quantity,
    avg_entry,
    realized_pnl,
    unrealized_pnl,
    last_price,
    exposure,
    strategy,
    reason,
    source_table
)
SELECT
    p.symbol,
    p.quantity,
    p.avg_entry,
    p.realized_pnl,
    p.unrealized_pnl,
    p.last_price,
    p.exposure,
    p.strategy,
    'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE',
    'positions_current_positive'
FROM positions p
WHERE p.quantity > 0
  AND lower(coalesce(p.strategy,'')) = 'paper_position_sync'
  AND NOT EXISTS (
      SELECT 1
      FROM trades t
      WHERE t.symbol = p.symbol
  );

-- Forensic archive from Agent 5 confirmed pre-cleanup evidence.
-- These rows were observed as orphan positive positions before the runtime guard zeroed/blocked them.
-- ON CONFLICT is not available because table has no unique key; avoid duplicate inserts by checking symbol+reason+source.
INSERT INTO orphan_position_archive (
    symbol, quantity, avg_entry, realized_pnl, unrealized_pnl,
    last_price, exposure, strategy, reason, source_table
)
SELECT *
FROM (
    VALUES
    ('BILL/USDT',   21.881485::double precision, 0.0685::double precision,   0::double precision, -0.049014527::double precision, 0.06626::double precision, 1.4472414::double precision, 'paper_position_sync', 'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE', 'agent5_pre_cleanup_evidence'),
    ('BOB/USDT',    346.17856::double precision, 0.005724::double precision, 0::double precision, -0.023193963::double precision, 0.005657::double precision, 1.9579859::double precision, 'paper_position_sync', 'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE', 'agent5_pre_cleanup_evidence'),
    ('GENIUS/USDT', 4.7687435::double precision, 0.41863::double precision,  0::double precision, -0.025560465::double precision, 0.41327::double precision, 1.9661529::double precision, 'paper_position_sync', 'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE', 'agent5_pre_cleanup_evidence'),
    ('BSB/USDT',    3.6340709::double precision, 0.55::double precision,     0::double precision, 0.0009811991::double precision, 0.55027::double precision, 1.9997202::double precision, 'paper_position_sync', 'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE', 'agent5_prior_audit_evidence')
) AS v(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, reason, source_table)
WHERE NOT EXISTS (
    SELECT 1
    FROM orphan_position_archive a
    WHERE a.symbol = v.symbol
      AND a.reason = v.reason
      AND a.source_table = v.source_table
);

-- Zero any remaining orphan positive paper_position_sync positions.
UPDATE positions p
SET
    quantity = 0,
    exposure = 0,
    unrealized_pnl = 0,
    strategy = CASE
        WHEN p.strategy IS NULL OR p.strategy = '' THEN 'orphan_closed_by_agent5_guard'
        WHEN p.strategy LIKE '%orphan_closed_by_agent5_guard%' THEN p.strategy
        ELSE p.strategy || '|orphan_closed_by_agent5_guard'
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE p.quantity > 0
  AND lower(coalesce(p.strategy,'')) LIKE 'paper_position_sync%'
  AND NOT EXISTS (
      SELECT 1
      FROM trades t
      WHERE t.symbol = p.symbol
  );

-- Force clean paper baseline snapshot after orphan cleanup.
INSERT INTO portfolio_snapshots (
    equity,
    cash,
    exposure,
    drawdown,
    regime
)
VALUES (
    100.0,
    100.0,
    0.0,
    0.0,
    'SIDEWAYS'
);

COMMIT;
