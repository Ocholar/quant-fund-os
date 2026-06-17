import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("NEGATIVE_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity
FROM positions
WHERE quantity < -0.00000001
"""))
print(rows if rows else "NONE")

print("\nRECENT_SIDEWAYS_GREEN_TO_RED_SELLS:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
WHERE side='sell'
  AND strategy='sideways_green_to_red_exit'
ORDER BY id DESC
LIMIT 10
"""))
print(rows if rows else "NONE")

print("\nRECENT_SELLS:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
WHERE side='sell'
ORDER BY id DESC
LIMIT 10
"""))
print(rows if rows else "NONE")

print("\nBUY_SELL_COUNTS:")
rows = list(cur.execute("""
SELECT side, COUNT(*)
FROM trades
GROUP BY side
ORDER BY side
"""))
print(rows if rows else "NONE")
