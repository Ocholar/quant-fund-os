import sqlite3
import os

before_id = int(os.environ["BEFORE_ID"])

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("NEW_TRADES_AFTER_PHASE3A3:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy,
       COALESCE(is_exit, 'MISSING') AS is_exit,
       COALESCE(exit_reason, 'MISSING') AS exit_reason,
       created_at
FROM trades
WHERE id > ?
ORDER BY id ASC
""", (before_id,)))
print(rows if rows else "NONE")

print("\nSELLS_WITH_BAD_EXIT_ACCOUNTING:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, strategy, is_exit, exit_reason, created_at
FROM trades
WHERE id > ?
  AND side='sell'
  AND (
      COALESCE(is_exit, 0) = 0
      OR exit_reason IS NULL
      OR TRIM(exit_reason) = ''
  )
ORDER BY id ASC
""", (before_id,)))
print(rows if rows else "NONE")

print("\nSELL_ONLY_LIFECYCLE_CHECK:")
buys = cur.execute("SELECT COUNT(*) FROM trades WHERE id > ? AND side='buy'", (before_id,)).fetchone()[0]
sells = cur.execute("SELECT COUNT(*) FROM trades WHERE id > ? AND side='sell'", (before_id,)).fetchone()[0]
print("buys", buys, "sells", sells)

print("\nNEW_DUPLICATE_SELL_PATTERNS:")
rows = list(cur.execute("""
SELECT symbol, side, quantity, strategy, COUNT(*) AS c
FROM trades
WHERE id > ?
  AND side='sell'
GROUP BY symbol, side, quantity, strategy
HAVING c > 1
ORDER BY c DESC
""", (before_id,)))
print(rows if rows else "NONE")

print("\nNEGATIVE_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity
FROM positions
WHERE quantity < -0.00000001
"""))
print(rows if rows else "NONE")

print("\nOPEN_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
FROM positions
WHERE quantity > 0.00000001
ORDER BY symbol
"""))
print(rows if rows else "NONE")
