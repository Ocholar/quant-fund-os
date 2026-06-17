from core.db import engine
from sqlalchemy import text
with engine.begin() as conn:
    row = conn.execute(text("SELECT equity, cash, exposure, drawdown, regime, created_at FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")).mappings().first()
    print(dict(row) if row else None)
