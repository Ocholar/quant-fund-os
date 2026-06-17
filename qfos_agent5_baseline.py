import sqlite3, json, time

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

def safe(q, default=None):
    try:
        row = cur.execute(q).fetchone()
        return row[0] if row else default
    except Exception:
        return default

baseline = {
    "max_trade_id": safe("SELECT COALESCE(MAX(id), 0) FROM trades", 0),
    "trade_count": safe("SELECT COUNT(*) FROM trades", 0),
    "buy_count": safe("SELECT COUNT(*) FROM trades WHERE side='buy'", 0),
    "sell_count": safe("SELECT COUNT(*) FROM trades WHERE side='sell'", 0),
    "open_position_count": safe("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001", 0),
    "negative_position_count": safe("SELECT COUNT(*) FROM positions WHERE quantity < -0.00000001", 0),
    "snapshot_count": safe("SELECT COUNT(*) FROM portfolio_snapshots", 0),
    "ts": time.time(),
}

print(json.dumps(baseline, indent=2))
