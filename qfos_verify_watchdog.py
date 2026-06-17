import sqlite3

db="/app/data/quant.db"
conn=sqlite3.connect(db)
cur=conn.cursor()

print("\nZEC POSITION ROW")
for r in cur.execute("""
SELECT symbol, quantity, avg_entry, last_price, exposure, realized_pnl, unrealized_pnl, strategy, updated_at
FROM positions
WHERE symbol='ZEC/USDT'
"""):
    print(r)

print("\nOPEN POSITIONS")
for r in cur.execute("""
SELECT symbol, quantity, avg_entry, last_price, exposure, realized_pnl, unrealized_pnl, strategy, updated_at
FROM positions
WHERE quantity > 0
ORDER BY unrealized_pnl ASC
"""):
    print(r)

print("\nRECENT TRADES")
for r in cur.execute("""
SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
FROM trades
ORDER BY id DESC
LIMIT 10
"""):
    print(r)

print("\nSYMBOL QUARANTINE")
for r in cur.execute("""
SELECT symbol, reason, blocked_until, created_at
FROM symbol_quarantine
ORDER BY blocked_until DESC
"""):
    print(r)

conn.close()
