import sqlite3, json, time

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

def scalar(q, default=0):
    try:
        row = cur.execute(q).fetchone()
        return row[0] if row else default
    except Exception:
        return default

baseline = {
    "max_trade_id": scalar("SELECT COALESCE(MAX(id), 0) FROM trades", 0),
    "trade_count": scalar("SELECT COUNT(*) FROM trades", 0),
    "buy_count": scalar("SELECT COUNT(*) FROM trades WHERE side='buy'", 0),
    "sell_count": scalar("SELECT COUNT(*) FROM trades WHERE side='sell'", 0),
    "open_position_count": scalar("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001", 0),
    "negative_position_count": scalar("SELECT COUNT(*) FROM positions WHERE quantity < -0.00000001", 0),
    "snapshot_count": scalar("SELECT COUNT(*) FROM portfolio_snapshots", 0),
    "cash": None,
    "exposure": None,
    "ts": time.time(),
}

try:
    row = cur.execute("""
        SELECT cash, exposure
        FROM portfolio_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    if row:
        baseline["cash"] = row[0]
        baseline["exposure"] = row[1]
except Exception:
    pass

print(json.dumps(baseline, indent=2))
