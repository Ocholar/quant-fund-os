import time
from sqlalchemy import text
from core.config import settings
from core.db import engine
from core.portfolio import Portfolio
from core.regime import detect_regime
from core.risk_engine import RiskEngine
from data.ingestion import PaperMarketData
from data.feature_store import FeatureStore
from execution.executor import PaperExecutor
from ai.autonomous_agent import AutonomousFundAgent
from services.metrics import trades_total, equity_gauge, drawdown_gauge

portfolio = Portfolio()
market = PaperMarketData(settings.symbol_list)
features = FeatureStore()
risk = RiskEngine()
agent = AutonomousFundAgent(risk, PaperExecutor())

print("Quant Fund OS starting. LIVE_TRADING=", settings.live_trading)

while True:
    tick = market.tick()
    prices = tick["prices"]
    features.update(prices)
    f_by_symbol = {s: features.features(s) for s in settings.symbol_list}
    ready = [f for f in f_by_symbol.values() if f.get("ready")]
    avg_vol = sum(f["volatility"] for f in ready) / len(ready) if ready else 0
    avg_trend = sum(abs(f["trend"]) for f in ready) / len(ready) if ready else 0
    regime = detect_regime(avg_vol, avg_trend)
    risk.tune(regime)
    equity = portfolio.mark_to_market(prices)
    state = {"prices": prices, "features": f_by_symbol, "equity": equity, "regime": regime}
    result = agent.run_cycle(state)

    with engine.begin() as conn:
        for fill in result.get("orders", []):
            trades_total.inc()
            conn.execute(text("""
                INSERT INTO trades(symbol, side, quantity, expected_price, fill_price, slippage_bps, pnl, strategy, confidence, live)
                VALUES(:symbol, :side, :quantity, :expected_price, :fill_price, :slippage_bps, 0, :strategy, :confidence, :live)
            """), fill | {"live": settings.live_trading})
        conn.execute(text("""
            INSERT INTO portfolio_snapshots(equity, cash, exposure, drawdown, regime)
            VALUES(:equity, :cash, :exposure, :drawdown, :regime)
        """), {"equity": equity, "cash": portfolio.cash, "exposure": 0, "drawdown": portfolio.drawdown, "regime": regime})
    equity_gauge.set(equity)
    drawdown_gauge.set(portfolio.drawdown)
    print({"regime": regime, "equity": round(equity,2), "orders": len(result.get("orders", [])), "status": result["status"]})
    time.sleep(settings.trade_interval_seconds)
