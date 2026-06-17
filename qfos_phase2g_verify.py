import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("ETHFI_XMR_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
FROM positions
WHERE symbol IN ('ETHFI/USDT', 'XMR/USDT')
ORDER BY symbol
"""))
print(rows if rows else "NONE")

print("\nNEGATIVE_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity
FROM positions
WHERE quantity < -0.00000001
"""))
print(rows if rows else "NONE")

print("\nDUPLICATE_SELL_PATTERNS_RECENT:")
rows = list(cur.execute("""
SELECT symbol, side, quantity, strategy, COUNT(*) AS c
FROM trades
WHERE id >= 930
  AND side='sell'
GROUP BY symbol, side, quantity, strategy
HAVING c > 1
ORDER BY c DESC
"""))
print(rows if rows else "NONE")
