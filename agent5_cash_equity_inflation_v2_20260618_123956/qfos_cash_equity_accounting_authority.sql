BEGIN;

ALTER TABLE portfolio_snapshots
    ADD COLUMN IF NOT EXISTS realized_pnl DOUBLE PRECISION DEFAULT 0;

ALTER TABLE portfolio_snapshots
    ADD COLUMN IF NOT EXISTS unrealized_pnl DOUBLE PRECISION DEFAULT 0;

ALTER TABLE portfolio_snapshots
    ADD COLUMN IF NOT EXISTS total_pnl DOUBLE PRECISION DEFAULT 0;

CREATE TABLE IF NOT EXISTS qfos_accounting_repair_audit (
    id SERIAL PRIMARY KEY,
    audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    phase TEXT NOT NULL,
    expected_cash DOUBLE PRECISION,
    actual_cash DOUBLE PRECISION,
    expected_exposure DOUBLE PRECISION,
    actual_exposure DOUBLE PRECISION,
    expected_equity DOUBLE PRECISION,
    actual_equity DOUBLE PRECISION,
    cash_delta DOUBLE PRECISION,
    exposure_delta DOUBLE PRECISION,
    equity_delta DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    total_pnl DOUBLE PRECISION,
    total_buy_cost DOUBLE PRECISION,
    total_sell_proceeds DOUBLE PRECISION,
    note TEXT
);

-- Stop repeated strategy pollution from the previous guard.
UPDATE positions
SET strategy = regexp_replace(strategy, '(\|net_qty_guard)+', '', 'g')
WHERE strategy LIKE '%net_qty_guard%';

-- Replace the position guard so it no longer appends repeated net_qty_guard.
CREATE OR REPLACE FUNCTION qfos_position_lineage_guard_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_buy_qty DOUBLE PRECISION := 0;
    v_sell_qty DOUBLE PRECISION := 0;
    v_net_qty DOUBLE PRECISION := 0;
    v_weighted_avg DOUBLE PRECISION := 0;
    v_last_trade_price DOUBLE PRECISION := 0;
    v_strategy_lower TEXT := '';
    v_has_approved_marker BOOLEAN := FALSE;
    v_effective_price DOUBLE PRECISION := 0;
BEGIN
    v_strategy_lower := lower(coalesce(NEW.strategy, ''));

    v_has_approved_marker :=
        v_strategy_lower LIKE '%seeded%'
        OR v_strategy_lower LIKE '%seed%'
        OR v_strategy_lower LIKE '%test%'
        OR v_strategy_lower LIKE '%reconciled%'
        OR v_strategy_lower LIKE '%approved_migration%'
        OR v_strategy_lower LIKE '%pm_approved_migration%'
        OR v_strategy_lower LIKE '%lineage_status=approved%'
        OR v_strategy_lower LIKE '%source=reconciled%';

    SELECT
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity * fill_price ELSE 0 END)
                 / NULLIF(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0), 0),
        COALESCE((
            SELECT fill_price
            FROM trades
            WHERE symbol = NEW.symbol
            ORDER BY id DESC
            LIMIT 1
        ), 0)
    INTO
        v_buy_qty,
        v_sell_qty,
        v_weighted_avg,
        v_last_trade_price
    FROM trades
    WHERE symbol = NEW.symbol;

    v_net_qty := COALESCE(v_buy_qty,0) - COALESCE(v_sell_qty,0);
    v_effective_price := COALESCE(NULLIF(NEW.last_price,0), NULLIF(v_last_trade_price,0), NULLIF(v_weighted_avg,0), NULLIF(NEW.avg_entry,0), 0);

    IF v_net_qty <= 0.00000001 THEN
        IF COALESCE(NEW.quantity,0) > 0.00000001 AND NOT v_has_approved_marker THEN
            RAISE NOTICE '[QFOS_POSITION_LINEAGE_GUARD] blocked_positive_position_without_trade_lineage symbol=% requested_qty=% strategy=%',
                NEW.symbol, NEW.quantity, NEW.strategy;

            NEW.quantity := 0;
            NEW.exposure := 0;
            NEW.unrealized_pnl := 0;
            IF NEW.strategy IS NULL OR NEW.strategy = '' THEN
                NEW.strategy := 'blocked_no_trade_lineage';
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    IF NOT v_has_approved_marker AND ABS(COALESCE(NEW.quantity,0) - v_net_qty) > 0.00000001 THEN
        RAISE NOTICE '[QFOS_POSITION_NET_QTY_GUARD] corrected_position_qty symbol=% requested_qty=% ledger_net_qty=% strategy=%',
            NEW.symbol, NEW.quantity, v_net_qty, NEW.strategy;

        NEW.quantity := v_net_qty;
        NEW.avg_entry := v_weighted_avg;

        IF v_effective_price <= 0 THEN
            v_effective_price := v_weighted_avg;
        END IF;

        NEW.last_price := v_effective_price;
        NEW.exposure := NEW.quantity * v_effective_price;
        NEW.unrealized_pnl := NEW.quantity * (v_effective_price - v_weighted_avg);

        -- Keep strategy stable. Do not append repeated guard markers.
        IF NEW.strategy IS NULL OR NEW.strategy = '' THEN
            NEW.strategy := 'ledger_net_qty_guard';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS qfos_positions_biu_lineage_guard ON positions;

CREATE TRIGGER qfos_positions_biu_lineage_guard
BEFORE INSERT OR UPDATE ON positions
FOR EACH ROW
EXECUTE FUNCTION qfos_position_lineage_guard_trigger();

-- Ledger-derived paper accounting function.
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

-- Audit current latest snapshot before repair.
INSERT INTO qfos_accounting_repair_audit (
    phase,
    expected_cash,
    actual_cash,
    expected_exposure,
    actual_exposure,
    expected_equity,
    actual_equity,
    cash_delta,
    exposure_delta,
    equity_delta,
    realized_pnl,
    unrealized_pnl,
    total_pnl,
    total_buy_cost,
    total_sell_proceeds,
    note
)
SELECT
    'before_cash_equity_repair',
    a.expected_cash,
    s.cash,
    a.expected_exposure,
    s.exposure,
    a.expected_equity,
    s.equity,
    s.cash - a.expected_cash,
    s.exposure - a.expected_exposure,
    s.equity - a.expected_equity,
    a.realized_pnl,
    a.unrealized_pnl,
    a.total_pnl,
    a.total_buy_cost,
    a.total_sell_proceeds,
    'cash/equity inflation preimage'
FROM qfos_current_ledger_accounting() a
LEFT JOIN LATERAL (
    SELECT *
    FROM portfolio_snapshots
    ORDER BY id DESC
    LIMIT 1
) s ON TRUE;

-- Snapshot guard: every future snapshot is forced to ledger-derived accounting.
CREATE OR REPLACE FUNCTION qfos_portfolio_snapshot_accounting_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    a RECORD;
BEGIN
    SELECT *
    INTO a
    FROM qfos_current_ledger_accounting()
    LIMIT 1;

    NEW.cash := a.expected_cash;
    NEW.exposure := a.expected_exposure;
    NEW.equity := a.expected_equity;
    NEW.realized_pnl := a.realized_pnl;
    NEW.unrealized_pnl := a.unrealized_pnl;
    NEW.total_pnl := a.total_pnl;

    IF a.expected_equity >= 100.0 THEN
        NEW.drawdown := 0.0;
    ELSE
        NEW.drawdown := (a.expected_equity - 100.0) / 100.0;
    END IF;

    IF NEW.regime IS NULL OR NEW.regime = '' THEN
        NEW.regime := 'SIDEWAYS';
    END IF;

    RAISE NOTICE '[QFOS_ACCOUNTING_GUARD] cash=% exposure=% equity=% realized=% unrealized=% total=%',
        NEW.cash, NEW.exposure, NEW.equity, NEW.realized_pnl, NEW.unrealized_pnl, NEW.total_pnl;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS qfos_portfolio_snapshots_biu_accounting_guard ON portfolio_snapshots;

CREATE TRIGGER qfos_portfolio_snapshots_biu_accounting_guard
BEFORE INSERT OR UPDATE ON portfolio_snapshots
FOR EACH ROW
EXECUTE FUNCTION qfos_portfolio_snapshot_accounting_guard();

-- Insert corrected snapshot now. The trigger will overwrite 0 values with ledger-derived values.
INSERT INTO portfolio_snapshots (
    equity,
    cash,
    exposure,
    drawdown,
    regime,
    realized_pnl,
    unrealized_pnl,
    total_pnl
)
VALUES (
    0,
    0,
    0,
    0,
    'SIDEWAYS',
    0,
    0,
    0
);

-- Audit after repair.
INSERT INTO qfos_accounting_repair_audit (
    phase,
    expected_cash,
    actual_cash,
    expected_exposure,
    actual_exposure,
    expected_equity,
    actual_equity,
    cash_delta,
    exposure_delta,
    equity_delta,
    realized_pnl,
    unrealized_pnl,
    total_pnl,
    total_buy_cost,
    total_sell_proceeds,
    note
)
SELECT
    'after_cash_equity_repair',
    a.expected_cash,
    s.cash,
    a.expected_exposure,
    s.exposure,
    a.expected_equity,
    s.equity,
    s.cash - a.expected_cash,
    s.exposure - a.expected_exposure,
    s.equity - a.expected_equity,
    a.realized_pnl,
    a.unrealized_pnl,
    a.total_pnl,
    a.total_buy_cost,
    a.total_sell_proceeds,
    'cash/equity inflation postimage'
FROM qfos_current_ledger_accounting() a
LEFT JOIN LATERAL (
    SELECT *
    FROM portfolio_snapshots
    ORDER BY id DESC
    LIMIT 1
) s ON TRUE;

COMMIT;
