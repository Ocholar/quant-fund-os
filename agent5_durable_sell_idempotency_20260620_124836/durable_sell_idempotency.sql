BEGIN;

ALTER TABLE trades
ADD COLUMN IF NOT EXISTS lifecycle_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS qfos_trades_sell_lifecycle_key_uq
ON trades (lifecycle_key)
WHERE lifecycle_key IS NOT NULL;

COMMIT;
