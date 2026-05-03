from fastapi import FastAPI
from sqlalchemy import text
from core.db import engine
from services.metrics import metrics_app

app = FastAPI(title="Quant Fund OS")
app.mount("/metrics", metrics_app)

@app.get("/")
def root():
    return {"name": "Quant Fund OS", "mode": "paper-first", "status": "running"}

@app.get("/trades")
def trades(limit: int = 50):
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT symbol, side, quantity, fill_price, slippage_bps, strategy, confidence, live, created_at FROM trades ORDER BY id DESC LIMIT :limit"), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]

@app.get("/portfolio")
def portfolio(limit: int = 100):
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT equity, cash, exposure, drawdown, regime, created_at FROM portfolio_snapshots ORDER BY id DESC LIMIT :limit"), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]
