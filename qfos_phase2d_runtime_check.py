import sqlite3
import os

before_id = int(os.environ["BEFORE_ID"])

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("NEW_TRADES:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
WHERE id > ?
ORDER BY id ASC
""", (before_id,)))
print(rows if rows else "NONE")

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

print("\nXMR_ETHFI_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
FROM positions
WHERE symbol IN ('XMR/USDT', 'ETHFI/USDT')
ORDER BY symbol
"""))
print(rows if rows else "NONE")
