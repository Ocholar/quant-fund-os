import sqlite3

db="/app/data/quant.db"
conn=sqlite3.connect(db)
cur=conn.cursor()

print("\nOPEN POSITIONS")
for r in cur.execute("""
SELECT symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, strategy
FROM positions
WHERE quantity > 0
ORDER BY unrealized_pnl ASC
"""):
    print(r)

print("\nSYMBOL QUARANTINE")
for r in cur.execute("""
SELECT symbol, reason, blocked_until, created_at
FROM symbol_quarantine
ORDER BY blocked_until DESC
"""):
    print(r)

print("\nSTRATEGY QUARANTINE")
for r in cur.execute("""
SELECT strategy, reason, blocked_until, created_at
FROM strategy_quarantine
ORDER BY blocked_until DESC
"""):
    print(r)

print("\nRECENT TRADES")
for r in cur.execute("""
SELECT id, symbol, side, pnl, strategy, created_at
FROM trades
ORDER BY id DESC
LIMIT 20
"""):
    print(r)

conn.close()
