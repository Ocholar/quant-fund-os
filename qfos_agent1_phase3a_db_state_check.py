import sqlite3, json, os

db_path = "data/quant.db"
if not os.path.exists(db_path):
    db_path = "/app/data/quant.db"

con = sqlite3.connect(db_path)
cur = con.cursor()

out = {}

for table in ["trades", "positions", "portfolio_snapshots", "profit_engine_state", "position_peak_state", "symbol_quarantine", "strategy_quarantine"]:
    out[table] = {}
    try:
        out[table]["schema"] = cur.execute(f"PRAGMA table_info({table})").fetchall()
        out[table]["count"] = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        out[table]["rows"] = cur.execute(f"SELECT * FROM {table} LIMIT 20").fetchall()
    except Exception as e:
        out[table]["error"] = str(e)

try:
    out["negative_positions"] = cur.execute("""
        SELECT symbol, quantity
        FROM positions
        WHERE quantity < -0.00000001
    """).fetchall()
except Exception as e:
    out["negative_positions_error"] = str(e)

try:
    out["open_positions"] = cur.execute("""
        SELECT symbol, quantity, avg_entry, last_price, exposure, strategy, updated_at
        FROM positions
        WHERE ABS(COALESCE(quantity, 0)) > 0.00000001
    """).fetchall()
except Exception as e:
    out["open_positions_error"] = str(e)

try:
    out["duplicate_sell_groups"] = cur.execute("""
        SELECT symbol, quantity, fill_price, strategy, COUNT(*) AS n
        FROM trades
        WHERE side='sell'
        GROUP BY symbol, quantity, fill_price, strategy
        HAVING COUNT(*) > 1
    """).fetchall()
except Exception as e:
    out["duplicate_sell_groups_error"] = str(e)

print(json.dumps(out, indent=2, default=str))
con.close()
