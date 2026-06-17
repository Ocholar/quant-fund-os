import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

print("BASELINE_MAX_TRADE_ID:")
print(cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0])

print("\nEDEN_POSITION_BEFORE_OBSERVATION:")
rows = list(cur.execute("""
SELECT symbol, quantity, avg_entry, unrealized_pnl, realized_pnl, exposure, strategy, updated_at
FROM positions
WHERE symbol='EDEN/USDT'
"""))
print(rows if rows else "NO EDEN POSITION")

print("\nLATEST_EDEN_TRADE:")
rows = list(cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
WHERE symbol='EDEN/USDT'
ORDER BY id DESC
LIMIT 1
"""))
print(rows if rows else "NO EDEN TRADE")
