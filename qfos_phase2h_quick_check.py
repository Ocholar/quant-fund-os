import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("MAX_TRADE_ID:")
print(cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0])

print("\nOPEN_POSITIONS:")
rows = list(cur.execute("""
SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
FROM positions
WHERE quantity > 0.00000001
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
