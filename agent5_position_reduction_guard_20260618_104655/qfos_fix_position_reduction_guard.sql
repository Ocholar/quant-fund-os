BEGIN;

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

    -- Case 1: no trade lineage exists. Positive executable position is forbidden unless explicitly approved.
    IF v_net_qty <= 0.00000001 THEN
        IF COALESCE(NEW.quantity,0) > 0.00000001 AND NOT v_has_approved_marker THEN
            RAISE NOTICE '[QFOS_POSITION_LINEAGE_GUARD] blocked_positive_position_without_trade_lineage symbol=% requested_qty=% strategy=%',
                NEW.symbol, NEW.quantity, NEW.strategy;

            NEW.quantity := 0;
            NEW.exposure := 0;
            NEW.unrealized_pnl := 0;
            NEW.strategy := COALESCE(NEW.strategy, '') || '|blocked_no_trade_lineage';
        END IF;

        RETURN NEW;
    END IF;

    -- Case 2: trade lineage says an open position MUST exist.
    -- Any attempted INSERT/UPDATE below net trade quantity is corrected.
    -- This blocks paper_position_sync or stale runtime writes from zeroing/reducing GUA/TRIA/etc without SELL rows.
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
        NEW.strategy := COALESCE(NEW.strategy, '') || '|net_qty_guard';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS qfos_positions_biu_lineage_guard ON positions;

CREATE TRIGGER qfos_positions_biu_lineage_guard
BEFORE INSERT OR UPDATE ON positions
FOR EACH ROW
EXECUTE FUNCTION qfos_position_lineage_guard_trigger();

-- Rebuild all positions from ledger again after strengthening the guard.
SELECT qfos_rebuild_position_from_trade_ledger(symbol)
FROM (
    SELECT symbol FROM trades
    UNION
    SELECT symbol FROM positions
) s
ORDER BY symbol;

COMMIT;
