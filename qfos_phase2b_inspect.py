import sqlite3

DB = "/app/data/quant.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("=== TRADE ID 869 ===")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, expected_price, fill_price, pnl, strategy, created_at
FROM trades
WHERE id = 869
"""))
print(rows if rows else "NOT FOUND")

print("\n=== EDEN POSITION ROW ===")
rows = list(cur.execute("""
SELECT *
FROM positions
WHERE symbol = 'EDEN/USDT'
"""))
print(rows if rows else "NO EDEN POSITION ROW")

print("\n=== POSITIONS TABLE COLUMNS ===")
rows = list(cur.execute("PRAGMA table_info(positions)"))
print(rows)

print("\n=== TRADES TABLE COLUMNS ===")
rows = list(cur.execute("PRAGMA table_info(trades)"))
print(rows)

print("\n=== RECENT EDEN TRADES ===")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, expected_price, fill_price, pnl, strategy, created_at
FROM trades
WHERE symbol='EDEN/USDT'
ORDER BY id DESC
LIMIT 20
"""))
print(rows if rows else "NONE")

print("\n=== TRADE IDS 860-875 ===")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
WHERE id BETWEEN 860 AND 875
ORDER BY id ASC
"""))
print(rows if rows else "NONE")

print("\n=== CURRENT NEGATIVE POSITIONS ===")
rows = list(cur.execute("""
SELECT symbol, quantity
FROM positions
WHERE quantity < -0.00000001
"""))
print(rows if rows else "NONE")

print("\n=== CURRENT OPEN POSITIONS ===")
rows = list(cur.execute("""
SELECT symbol, quantity, avg_entry, unrealized_pnl, realized_pnl, exposure, strategy, updated_at
FROM positions
WHERE quantity > 0.00000001
ORDER BY symbol
"""))
print(rows if rows else "NONE")
