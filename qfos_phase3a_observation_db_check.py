import sqlite3
con = sqlite3.connect("data/quant.db")
cur = con.cursor()

print("\nCOUNTS")
for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        print(table, cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception as e:
        print(table, "ERR", e)

print("\nOPEN POSITIONS")
try:
    rows = cur.execute("""
        SELECT symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, updated_at
        FROM positions
        WHERE ABS(COALESCE(quantity, 0)) > 0.00000001
        ORDER BY symbol
    """).fetchall()
    print(rows if rows else "NONE")
except Exception as e:
    print("ERR", e)

print("\nNEGATIVE POSITIONS")
try:
    rows = cur.execute("""
        SELECT symbol, quantity
        FROM positions
        WHERE COALESCE(quantity, 0) < -0.00000001
    """).fetchall()
    print(rows if rows else "NONE")
except Exception as e:
    print("ERR", e)

print("\nBUY/SELL COUNTS")
try:
    print(cur.execute("""
        SELECT LOWER(side), COUNT(*)
        FROM trades
        GROUP BY LOWER(side)
    """).fetchall())
except Exception as e:
    print("ERR", e)

print("\nDUPLICATE SELL GROUPS")
try:
    rows = cur.execute("""
        SELECT symbol, quantity, strategy, COUNT(*) AS n
        FROM trades
        WHERE LOWER(side) = 'sell'
        GROUP BY symbol, quantity, strategy
        HAVING COUNT(*) > 1
        ORDER BY n DESC
    """).fetchall()
    print(rows if rows else "NONE")
except Exception as e:
    print("ERR", e)

print("\nLATEST TRADES")
try:
    rows = cur.execute("""
        SELECT id, symbol, side, quantity, fill_price, strategy, pnl, created_at
        FROM trades
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()
    print(rows if rows else "NONE")
except Exception as e:
    print("ERR", e)

con.close()
