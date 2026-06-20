BEGIN;

CREATE TABLE IF NOT EXISTS qfos_dust_cleanup_audit (
    id BIGSERIAL PRIMARY KEY,
    cleanup_run TEXT NOT NULL,
    cleaned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT NOT NULL,
    quantity NUMERIC,
    source_row JSONB NOT NULL
);

WITH dust AS (
    SELECT
        symbol,
        quantity,
        to_jsonb(p) AS source_row
    FROM positions p
    WHERE ABS(COALESCE(quantity, 0)) > 0
      AND ABS(COALESCE(quantity, 0)) <= 0.0001
)
INSERT INTO qfos_dust_cleanup_audit (
    cleanup_run,
    symbol,
    quantity,
    source_row
)
SELECT
    :'cleanup_run',
    symbol,
    quantity,
    source_row
FROM dust;

DELETE FROM positions
WHERE ABS(COALESCE(quantity, 0)) > 0
  AND ABS(COALESCE(quantity, 0)) <= 0.0001;

-- Clear derived runtime/lifecycle state first.
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'qfos_exit_lifecycle_state',
        'profit_engine_peaks',
        'profit_engine_state',
        'strategy_scores',
        'strategy_quarantine',
        'symbol_quarantine',
        'trade_counts',
        'quarantine'
    ]
    LOOP
        IF to_regclass('public.' || tbl) IS NOT NULL THEN
            EXECUTE format('DELETE FROM %I', tbl);
        END IF;
    END LOOP;
END
$$;

-- Delete history first; existing trigger logic may rebuild positions from it.
DELETE FROM trades;
DELETE FROM positions;
DELETE FROM portfolio_snapshots;

INSERT INTO portfolio_snapshots (
    cash,
    equity,
    exposure,
    realized_pnl,
    unrealized_pnl,
    total_pnl,
    drawdown,
    created_at
)
VALUES (
    100.0,
    100.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    CURRENT_TIMESTAMP
);

COMMIT;
