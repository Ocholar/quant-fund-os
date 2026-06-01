import sqlite3
import os

db_path = 'data/quant.db'
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Get all tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]

clear_tables = ['trades', 'positions', 'portfolio', 'portfolio_snapshots', 'strategy_scores', 'symbol_quarantine']

for t in clear_tables:
    if t in tables:
        print(f"Clearing table: {t}")
        cur.execute(f"DELETE FROM {t}")

# Re-insert clean portfolio
cur.execute("INSERT INTO portfolio (equity, cash, exposure, drawdown, regime) VALUES (100.0, 100.0, 0.0, 0.0, 'SIDEWAYS')")

conn.commit()
conn.close()
print("Database reset to 100.0 equity.")
