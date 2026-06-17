import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("EDEN_POSITION:")
rows = list(cur.execute("""
SELECT symbol, quantity, avg_entry, unrealized_pnl, realized_pnl, exposure, strategy, updated_at
FROM positions
WHERE symbol='EDEN/USDT'
"""))
print(rows if rows else "NO EDEN POSITION")

print("\nDUPLICATE_SELL_PATTERNS_RECENT:")
rows = list(cur.execute("""
SELECT symbol, side, quantity, strategy, COUNT(*) AS c
FROM trades
WHERE id >= 869
  AND side='sell'
GROUP BY symbol, side, quantity, strategy
HAVING c > 1
ORDER BY c DESC
"""))
print(rows if rows else "NONE")

print("\nNEGATIVE_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity
FROM positions
WHERE quantity < -0.00000001
"""))
print(rows if rows else "NONE")
