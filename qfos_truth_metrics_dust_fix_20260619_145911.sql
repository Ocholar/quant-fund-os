BEGIN;

CREATE TABLE IF NOT EXISTS qfos_dust_position_audit (
    id BIGSERIAL PRIMARY KEY,
    cleanup_run TEXT NOT NULL,
    cleaned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity NUMERIC NOT NULL,
    reference_price NUMERIC NOT NULL,
    notional NUMERIC NOT NULL,
    source_row JSONB NOT NULL
);

CREATE OR REPLACE FUNCTION qfos_reconcile_position_dust_v1()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_price NUMERIC;
    v_notional NUMERIC;
    v_threshold NUMERIC := 0.01;
BEGIN
    IF NEW.quantity IS NULL OR ABS(NEW.quantity) = 0 THEN
        RETURN NEW;
    END IF;

    v_price := COALESCE(
        NULLIF(NEW.last_price, 0),
        NULLIF(NEW.avg_entry, 0),
        0
    );

    v_notional := ABS(NEW.quantity) * ABS(v_price);

    IF v_price > 0 AND v_notional <= v_threshold THEN
        INSERT INTO qfos_dust_position_audit (
            cleanup_run,
            source,
            symbol,
            quantity,
            reference_price,
            notional,
            source_row
        )
        VALUES (
            'runtime_trigger',
            'qfos_reconcile_position_dust_v1',
            NEW.symbol,
            NEW.quantity,
            v_price,
            v_notional,
            to_jsonb(NEW)
        );

        DELETE FROM positions
        WHERE symbol = NEW.symbol
          AND ABS(COALESCE(quantity, 0)) * ABS(
              COALESCE(NULLIF(last_price, 0), NULLIF(avg_entry, 0), 0)
          ) <= v_threshold;

        RAISE NOTICE
            '[QFOS_DUST_POSITION_RECONCILED] symbol=% qty=% notional=% threshold=%',
            NEW.symbol, NEW.quantity, v_notional, v_threshold;

        RETURN NULL;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS qfos_reconcile_position_dust_after_write_v1 ON positions;

CREATE TRIGGER qfos_reconcile_position_dust_after_write_v1
AFTER INSERT OR UPDATE OF quantity, avg_entry, last_price
ON positions
FOR EACH ROW
EXECUTE FUNCTION qfos_reconcile_position_dust_v1();

-- Sweep any existing economically negligible residuals once.
WITH dust AS (
    DELETE FROM positions
    WHERE ABS(COALESCE(quantity, 0)) > 0
      AND ABS(COALESCE(quantity, 0)) * ABS(
          COALESCE(NULLIF(last_price, 0), NULLIF(avg_entry, 0), 0)
      ) <= 0.01
    RETURNING *
)
INSERT INTO qfos_dust_position_audit (
    cleanup_run,
    source,
    symbol,
    quantity,
    reference_price,
    notional,
    source_row
)
SELECT
    'install_sweep',
    'qfos_reconcile_position_dust_v1',
    symbol,
    quantity,
    COALESCE(NULLIF(last_price, 0), NULLIF(avg_entry, 0), 0),
    ABS(quantity) * ABS(
        COALESCE(NULLIF(last_price, 0), NULLIF(avg_entry, 0), 0)
    ),
    to_jsonb(dust)
FROM dust;

COMMIT;
