BEGIN;

CREATE TABLE IF NOT EXISTS qfos_position_cost_basis_audit (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT NOT NULL,
    open_qty DOUBLE PRECISION NOT NULL,
    old_avg_entry DOUBLE PRECISION NOT NULL,
    new_avg_entry DOUBLE PRECISION NOT NULL,
    last_price DOUBLE PRECISION NOT NULL,
    latest_buy_id BIGINT,
    latest_sell_id BIGINT,
    reason TEXT NOT NULL
);

WITH candidate AS (
    SELECT
        p.symbol,
        p.quantity AS open_qty,
        p.avg_entry AS old_avg_entry,
        p.last_price,
        lb.id AS latest_buy_id,
        lb.quantity AS latest_buy_qty,
        lb.fill_price AS latest_buy_price,
        ls.id AS latest_sell_id
    FROM positions p
    JOIN LATERAL (
        SELECT id, quantity, fill_price
        FROM trades
        WHERE symbol = p.symbol
          AND lower(side) = 'buy'
        ORDER BY id DESC
        LIMIT 1
    ) lb ON true
    LEFT JOIN LATERAL (
        SELECT id
        FROM trades
        WHERE symbol = p.symbol
          AND lower(side) = 'sell'
        ORDER BY id DESC
        LIMIT 1
    ) ls ON true
    WHERE p.symbol = 'HYPE/USDT'
      AND p.quantity > 0.00000001
      AND (ls.id IS NULL OR lb.id > ls.id)
      AND abs(p.quantity - lb.quantity) <= greatest(0.00000001, abs(lb.quantity) * 0.00001)
      AND abs(p.avg_entry - lb.fill_price) > 0.00000001
),
audit AS (
    INSERT INTO qfos_position_cost_basis_audit (
        symbol,
        open_qty,
        old_avg_entry,
        new_avg_entry,
        last_price,
        latest_buy_id,
        latest_sell_id,
        reason
    )
    SELECT
        symbol,
        open_qty,
        old_avg_entry,
        latest_buy_price,
        last_price,
        latest_buy_id,
        latest_sell_id,
        'single_fresh_open_lot_matches_latest_buy'
    FROM candidate
    RETURNING symbol
)
UPDATE positions p
SET
    avg_entry = c.latest_buy_price,
    exposure = p.quantity * coalesce(nullif(p.last_price, 0), c.latest_buy_price),
    unrealized_pnl = (
        coalesce(nullif(p.last_price, 0), c.latest_buy_price) - c.latest_buy_price
    ) * p.quantity,
    strategy = coalesce((
        select strategy
        from trades
        where id = c.latest_buy_id
    ), p.strategy),
    updated_at = CURRENT_TIMESTAMP
FROM candidate c
WHERE p.symbol = c.symbol;

COMMIT;
