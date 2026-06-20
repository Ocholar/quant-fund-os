BEGIN;

DO $$
DECLARE
    tbl text;
    candidates text[] := ARRAY[
        'trades',
        'positions',
        'portfolio_snapshots',
        'strategy_scores',
        'strategy_quarantine',
        'symbol_quarantine',
        'qfos_exit_decision_audit',
        'qfos_position_repair_audit',
        'qfos_accounting_repair_audit',
        'profit_engine_peaks',
        'profit_engine_state',
        'qfos_exit_lifecycle_state'
    ];
BEGIN
    FOREACH tbl IN ARRAY candidates LOOP
        IF to_regclass('public.' || tbl) IS NOT NULL THEN
            EXECUTE format('TRUNCATE TABLE public.%I RESTART IDENTITY CASCADE', tbl);
            RAISE NOTICE 'TRUNCATED %', tbl;
        ELSE
            RAISE NOTICE 'SKIPPED missing table %', tbl;
        END IF;
    END LOOP;
END $$;

INSERT INTO portfolio_snapshots (
  equity,
  cash,
  exposure,
  drawdown,
  regime,
  created_at
)
VALUES (
  100.0,
  100.0,
  0.0,
  0.0,
  'SIDEWAYS',
  CURRENT_TIMESTAMP
);

COMMIT;
