BEGIN;

-- Audit table for before/after repair evidence.
CREATE TABLE IF NOT EXISTS qfos_position_repair_audit (
    id SERIAL PRIMARY KEY,
    audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    phase TEXT NOT NULL,
    symbol TEXT,
    trade_net_qty DOUBLE PRECISION,
    position_qty DOUBLE PRECISION,
    avg_entry DOUBLE PRECISION,
    exposure DOUBLE PRECISION,
    note TEXT
);

-- Ensure ON CONFLICT(symbol) is valid and same-symbol rows cannot duplicate.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'positions'
          AND indexname = 'qfos_positions_symbol_unique_idx'
    ) THEN
        CREATE UNIQUE INDEX qfos_positions_symbol_unique_idx ON positions(symbol);
    END IF;
END $$;

-- Archive current mismatch before repair.
INSERT INTO qfos_position_repair_audit (
    phase, symbol, trade_net_qty, position_qty, avg_entry, exposure, note
)
WITH trade_net AS (
    SELECT
        symbol,
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE -quantity END),0) AS net_qty
    FROM trades
    GROUP BY symbol
)
SELECT
    'before_repair',
    COALESCE(t.symbol, p.symbol),
    COALESCE(t.net_qty,0),
    COALESCE(p.quantity,0),
    p.avg_entry,
    p.exposure,
    'BUY-to-position aggregation repair preimage'
FROM trade_net t
FULL OUTER JOIN positions p ON p.symbol=t.symbol
WHERE ABS(COALESCE(t.net_qty,0) - COALESCE(p.quantity,0)) > 0.00000001
   OR COALESCE(t.net_qty,0) > 0
   OR COALESCE(p.quantity,0) > 0;

-- Core repair/rebuild function.
-- It makes positions authoritative from trade ledger net quantity.
CREATE OR REPLACE FUNCTION qfos_rebuild_position_from_trade_ledger(p_symbol TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_buy_qty DOUBLE PRECISION := 0;
    v_sell_qty DOUBLE PRECISION := 0;
    v_net_qty DOUBLE PRECISION := 0;
    v_weighted_avg DOUBLE PRECISION := 0;
    v_last_price DOUBLE PRECISION := 0;
    v_strategy TEXT := 'ledger_rebuild_from_trades';
BEGIN
    SELECT
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity * fill_price ELSE 0 END)
                 / NULLIF(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0), 0),
        COALESCE((
            SELECT fill_price
            FROM trades
            WHERE symbol = p_symbol
            ORDER BY id DESC
            LIMIT 1
        ), 0),
        COALESCE((
            SELECT strategy
            FROM trades
            WHERE symbol = p_symbol
            ORDER BY id DESC
            LIMIT 1
        ), 'ledger_rebuild_from_trades')
    INTO
        v_buy_qty,
        v_sell_qty,
        v_weighted_avg,
        v_last_price,
        v_strategy
    FROM trades
    WHERE symbol = p_symbol;

    v_net_qty := COALESCE(v_buy_qty,0) - COALESCE(v_sell_qty,0);

    IF v_last_price <= 0 THEN
        v_last_price := v_weighted_avg;
    END IF;

    IF v_net_qty > 0.00000001 THEN
        INSERT INTO positions (
            symbol,
            quantity,
            avg_entry,
            realized_pnl,
            unrealized_pnl,
            last_price,
            exposure,
            strategy,
            updated_at
        )
        VALUES (
            p_symbol,
            v_net_qty,
            v_weighted_avg,
            0,
            v_net_qty * (v_last_price - v_weighted_avg),
            v_last_price,
            v_net_qty * v_last_price,
            v_strategy,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (symbol)
        DO UPDATE SET
            quantity = EXCLUDED.quantity,
            avg_entry = EXCLUDED.avg_entry,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            last_price = EXCLUDED.last_price,
            exposure = EXCLUDED.exposure,
            strategy = EXCLUDED.strategy,
            updated_at = CURRENT_TIMESTAMP;

        RAISE NOTICE '[QFOS_POSITION_REBUILD] symbol=% net_qty=% avg_entry=% last_price=%',
            p_symbol, v_net_qty, v_weighted_avg, v_last_price;
    ELSE
        UPDATE positions
        SET
            quantity = 0,
            exposure = 0,
            unrealized_pnl = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE symbol = p_symbol;

        RAISE NOTICE '[QFOS_POSITION_REBUILD] symbol=% closed_or_zero net_qty=%',
            p_symbol, v_net_qty;
    END IF;
END;
$$;

-- Trigger: every trade row mutation rebuilds that symbol's position.
CREATE OR REPLACE FUNCTION qfos_trade_position_rebuild_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_symbol TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_symbol := OLD.symbol;
    ELSE
        v_symbol := NEW.symbol;
    END IF;

    PERFORM qfos_rebuild_position_from_trade_ledger(v_symbol);
    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS qfos_trades_aiud_position_rebuild ON trades;

CREATE TRIGGER qfos_trades_aiud_position_rebuild
AFTER INSERT OR UPDATE OR DELETE ON trades
FOR EACH ROW
EXECUTE FUNCTION qfos_trade_position_rebuild_trigger();

-- Guard: no positions write may lower/overwrite executable quantity below trade net.
-- This protects against paper_position_sync or Python ON CONFLICT overwrites.
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

    IF COALESCE(NEW.quantity,0) > 0.00000001 AND NOT v_has_approved_marker THEN
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

        IF v_net_qty <= 0.00000001 THEN
            RAISE NOTICE '[QFOS_POSITION_LINEAGE_GUARD] blocked_positive_position_without_trade_lineage symbol=% requested_qty=% strategy=%',
                NEW.symbol, NEW.quantity, NEW.strategy;

            NEW.quantity := 0;
            NEW.exposure := 0;
            NEW.unrealized_pnl := 0;
            NEW.strategy := COALESCE(NEW.strategy, '') || '|blocked_no_trade_lineage';
            RETURN NEW;
        END IF;

        IF ABS(COALESCE(NEW.quantity,0) - v_net_qty) > 0.00000001 THEN
            RAISE NOTICE '[QFOS_POSITION_AGGREGATE_GUARD] corrected_position_qty symbol=% requested_qty=% ledger_net_qty=%',
                NEW.symbol, NEW.quantity, v_net_qty;

            NEW.quantity := v_net_qty;
            NEW.avg_entry := v_weighted_avg;

            IF COALESCE(NEW.last_price,0) <= 0 THEN
                NEW.last_price := COALESCE(NULLIF(v_last_trade_price,0), v_weighted_avg);
            END IF;

            NEW.exposure := NEW.quantity * COALESCE(NULLIF(NEW.last_price,0), v_weighted_avg);
            NEW.unrealized_pnl := NEW.quantity * (COALESCE(NULLIF(NEW.last_price,0), v_weighted_avg) - v_weighted_avg);
            NEW.strategy := COALESCE(NEW.strategy, '') || '|position_aggregate_guard';
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

-- Repair all existing position rows from ledger.
SELECT qfos_rebuild_position_from_trade_ledger(symbol)
FROM (
    SELECT symbol FROM trades
    UNION
    SELECT symbol FROM positions
) s
ORDER BY symbol;

-- Archive after-repair state.
INSERT INTO qfos_position_repair_audit (
    phase, symbol, trade_net_qty, position_qty, avg_entry, exposure, note
)
WITH trade_net AS (
    SELECT
        symbol,
        COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE -quantity END),0) AS net_qty
    FROM trades
    GROUP BY symbol
)
SELECT
    'after_repair',
    COALESCE(t.symbol, p.symbol),
    COALESCE(t.net_qty,0),
    COALESCE(p.quantity,0),
    p.avg_entry,
    p.exposure,
    'BUY-to-position aggregation repair postimage'
FROM trade_net t
FULL OUTER JOIN positions p ON p.symbol=t.symbol
WHERE COALESCE(t.net_qty,0) > 0
   OR COALESCE(p.quantity,0) > 0;

COMMIT;
