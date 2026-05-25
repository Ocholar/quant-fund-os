"""
Clean slate reset – wipes all trading history and restores $10.00 baseline.
"""
import sqlite3
import datetime

conn = sqlite3.connect("quant.db")
c = conn.cursor()

tables = ["trades", "positions", "portfolio_snapshots",
          "strategy_scores", "symbol_quarantine", "strategy_quarantine"]
for t in tables:
    try:
        c.execute(f"DELETE FROM {t}")
        print(f"  cleared {t}")
    except Exception as e:
        print(f"  skip {t}: {e}")

now = datetime.datetime.utcnow().isoformat()
c.execute(
    "INSERT INTO portfolio_snapshots (equity,cash,exposure,drawdown,regime,created_at) "
    "VALUES (10.0,10.0,0.0,0.0,'SIDEWAYS',?)",
    (now,)
)

conn.commit()
conn.close()
print("Clean slate done: $10.00, 0 trades, all quarantines cleared.")
