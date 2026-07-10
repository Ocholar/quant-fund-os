CREATE OR REPLACE FUNCTION qfos_current_ledger_accounting()
RETURNS TABLE (
    starting_cash DOUBLE PRECISION,
    total_buy_cost DOUBLE PRECISION,
    total_sell_proceeds DOUBLE PRECISION,
    buy_rows BIGINT,
    sell_rows BIGINT,
    open_positions BIGINT,
    expected_cash DOUBLE PRECISION,
    expected_exposure DOUBLE PRECISION,
    expected_equity DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    total_pnl DOUBLE PRECISION
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH
    params AS (
        SELECT 100.0::double precision AS initial_cash
    ),
    trade_totals AS (
        SELECT
            COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity * fill_price ELSE 0 END),0)::double precision AS buy_cost,
            COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity * fill_price ELSE 0 END),0)::double precision AS sell_proceeds,
            COUNT(*) FILTER (WHERE lower(side)='buy')::bigint AS buy_rows,
            COUNT(*) FILTER (WHERE lower(side)='sell')::bigint AS sell_rows
        FROM trades
    ),
    symbol_basis AS (
        SELECT
            symbol,
            COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END),0)::double precision AS buy_qty,
            COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END),0)::double precision AS sell_qty,
            COALESCE(
                SUM(CASE WHEN lower(side)='buy' THEN quantity * fill_price ELSE 0 END)
                / NULLIF(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END),0),
                0
            )::double precision AS avg_buy_price,
            COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity * fill_price ELSE 0 END),0)::double precision AS sell_proceeds
        FROM trades
        GROUP BY symbol
    ),
    realized AS (
        SELECT
            COALESCE(SUM(sell_proceeds - (sell_qty * avg_buy_price)),0)::double precision AS realized_pnl
        FROM symbol_basis
        WHERE sell_qty > 0
    ),
    open_pos AS (
        SELECT
            COUNT(*)::bigint AS open_positions,
            COALESCE(SUM(COALESCE(NULLIF(exposure,0), quantity * COALESCE(NULLIF(last_price,0), avg_entry))),0)::double precision AS exposure,
            COALESCE(SUM(quantity * avg_entry),0)::double precision AS open_cost_basis,
            COALESCE(SUM(quantity * (COALESCE(NULLIF(last_price,0), avg_entry) - avg_entry)),0)::double precision AS unrealized_by_price
        FROM positions
        WHERE quantity > 0.00000001
    )
    SELECT
        p.initial_cash AS starting_cash,
        tt.buy_cost AS total_buy_cost,
        tt.sell_proceeds AS total_sell_proceeds,
        tt.buy_rows AS buy_rows,
        tt.sell_rows AS sell_rows,
        op.open_positions AS open_positions,
        (p.initial_cash - tt.buy_cost + tt.sell_proceeds)::double precision AS expected_cash,
        op.exposure AS expected_exposure,
        (p.initial_cash - tt.buy_cost + tt.sell_proceeds + op.exposure)::double precision AS expected_equity,
        r.realized_pnl AS realized_pnl,
        (op.exposure - op.open_cost_basis)::double precision AS unrealized_pnl,
        (r.realized_pnl + (op.exposure - op.open_cost_basis))::double precision AS total_pnl
    FROM params p
    CROSS JOIN trade_totals tt
    CROSS JOIN open_pos op
    CROSS JOIN realized r;
END;
$$;

