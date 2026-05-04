import time
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core.config import settings
from core.db import engine
from core.portfolio import Portfolio
from core.regime import detect_regime
from core.risk_engine import RiskEngine
from data.ingestion import build_market_data
from data.feature_store import FeatureStore
from execution.executor import PaperExecutor
from ai.autonomous_agent import AutonomousFundAgent
from services.metrics import trades_total, equity_gauge, drawdown_gauge
from core.control import is_paused, ensure_runtime_dir
from services.telegram import send_telegram_alert

INITIAL_EQUITY = 10_000.0

MAX_TOTAL_EXPOSURE_PCT = 0.50
MAX_SYMBOL_EXPOSURE_PCT = 0.20
MAX_TRADES_PER_SYMBOL = 3
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.03
DAILY_LOSS_LIMIT_PCT = 0.05
COOLDOWN_SECONDS = 30

ALLOW_BUYS = True
ALLOW_SELLS = True


portfolio = Portfolio()
market = build_market_data(settings.symbol_list)
features = FeatureStore()
risk = RiskEngine()
executor = PaperExecutor()
agent = AutonomousFundAgent(risk, executor)

entry_prices = {}
trade_counts = {}
last_trade_time = {}

print("Quant Fund OS starting. LIVE_TRADING=", settings.live_trading)
print("Safety mode enabled. Paper trading only.")
ensure_runtime_dir()
send_telegram_alert("Quant Fund OS started. Paper mode active. Live trading is OFF.")
last_risk_status = None

def wait_for_database(max_attempts=30):
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
            print("Database connected.")
            return
        except OperationalError:
            print(f"Waiting for database... attempt {attempt}/{max_attempts}")
            time.sleep(2)

    raise RuntimeError("Database was not ready after waiting.")


def total_exposure(prices):
    return sum(
        qty * prices.get(symbol, 0.0)
        for symbol, qty in portfolio.positions.items()
    )


def symbol_exposure(symbol, prices):
    return portfolio.positions.get(symbol, 0.0) * prices.get(symbol, 0.0)


def can_buy(symbol, fill, prices, equity):
    if not ALLOW_BUYS:
        return False, "buys_disabled"

    if equity <= INITIAL_EQUITY * (1 - DAILY_LOSS_LIMIT_PCT):
        return False, "daily_loss_limit"

    now = time.time()

    if now - last_trade_time.get(symbol, 0) < COOLDOWN_SECONDS:
        return False, "cooldown"

    if trade_counts.get(symbol, 0) >= MAX_TRADES_PER_SYMBOL:
        return False, "max_trades_per_symbol"

    fill_value = float(fill["quantity"]) * float(fill["fill_price"])

    current_total_exposure = total_exposure(prices)
    if current_total_exposure + fill_value > equity * MAX_TOTAL_EXPOSURE_PCT:
        return False, "max_total_exposure"

    current_symbol_exposure = symbol_exposure(symbol, prices)
    if current_symbol_exposure + fill_value > equity * MAX_SYMBOL_EXPOSURE_PCT:
        return False, "max_symbol_exposure"

    return True, "approved"


def apply_buy(fill):
    symbol = fill["symbol"]
    qty = float(fill["quantity"])
    price = float(fill["fill_price"])
    cost = qty * price

    if portfolio.cash < cost:
        return False

    old_qty = portfolio.positions.get(symbol, 0.0)
    old_avg = entry_prices.get(symbol, price)

    new_qty = old_qty + qty
    new_avg = ((old_qty * old_avg) + (qty * price)) / new_qty

    portfolio.cash -= cost
    portfolio.positions[symbol] = new_qty
    entry_prices[symbol] = new_avg
    trade_counts[symbol] = trade_counts.get(symbol, 0) + 1
    last_trade_time[symbol] = time.time()

    return True


def apply_sell(symbol, qty, price, reason):
    held = portfolio.positions.get(symbol, 0.0)
    sell_qty = min(qty, held)

    if sell_qty <= 0:
        return None

    portfolio.cash += sell_qty * price
    portfolio.positions[symbol] = held - sell_qty

    if portfolio.positions[symbol] <= 0.00000001:
        portfolio.positions[symbol] = 0.0
        entry_prices.pop(symbol, None)
        trade_counts[symbol] = 0

    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": sell_qty,
        "expected_price": price,
        "fill_price": price,
        "slippage_bps": 0,
        "strategy": reason,
        "confidence": 1.0,
    }


def generate_sells(prices, regime):
    sells = []

    if not ALLOW_SELLS:
        return sells

    for symbol, qty in list(portfolio.positions.items()):
        if qty <= 0:
            continue

        price = prices.get(symbol)
        avg_entry = entry_prices.get(symbol)

        if not price or not avg_entry:
            continue

        change = (price - avg_entry) / avg_entry

        if change <= -STOP_LOSS_PCT:
            sell = apply_sell(symbol, qty, price, "stop_loss")
            if sell:
                sells.append(sell)

        elif change >= TAKE_PROFIT_PCT:
            sell = apply_sell(symbol, qty * 0.75, price, "take_profit")
            if sell:
                sells.append(sell)

        elif regime == "RISK_OFF":
            sell = apply_sell(symbol, qty, price, "risk_off_exit")
            if sell:
                sells.append(sell)

    return sells


def emergency_reduce_exposure(prices):
    sells = []

    equity_before_rebalance = portfolio.mark_to_market(prices)
    current_exposure = total_exposure(prices)
    max_allowed_exposure = equity_before_rebalance * MAX_TOTAL_EXPOSURE_PCT

    if current_exposure <= max_allowed_exposure:
        return sells

    excess = current_exposure - max_allowed_exposure

    for symbol, qty in list(portfolio.positions.items()):
        if excess <= 0:
            break

        price = prices.get(symbol)
        if not price or qty <= 0:
            continue

        position_value = qty * price
        value_to_sell = min(position_value, excess)
        qty_to_sell = value_to_sell / price

        sell = apply_sell(symbol, qty_to_sell, price, "emergency_exposure_reduction")
        if sell:
            sells.append(sell)

        excess -= value_to_sell

    return sells


def save_trade(conn, fill):
    conn.execute(text("""
        INSERT INTO trades(
            symbol, side, quantity, expected_price, fill_price,
            slippage_bps, pnl, strategy, confidence, live
        )
        VALUES(
            :symbol, :side, :quantity, :expected_price, :fill_price,
            :slippage_bps, 0, :strategy, :confidence, :live
        )
    """), fill | {"live": settings.live_trading})


wait_for_database()

while True:
    try:
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

        state = {
            "prices": prices,
            "features": f_by_symbol,
            "equity": equity,
            "regime": regime,
        }

        result = agent.run_cycle(state)
        proposed_fills = result.get("orders", [])

        applied_fills = []
        rejected = []

        for fill in proposed_fills:
            symbol = fill["symbol"]
            side = fill["side"]
            paused = is_paused()

            if paused:
                proposed_fills = []
                rejected.append({"symbol": "ALL", "reason": "kill_switch_active"})
            if side == "buy":
                approved, reason = can_buy(symbol, fill, prices, equity)
                if approved and apply_buy(fill):
                    applied_fills.append(fill)
                else:
                    rejected.append({"symbol": symbol, "reason": reason})

        applied_fills.extend(generate_sells(prices, regime))
        applied_fills.extend(emergency_reduce_exposure(prices))

        equity = portfolio.mark_to_market(prices)
        exposure = total_exposure(prices)

        with engine.begin() as conn:
            for fill in applied_fills:
                trades_total.inc()
                save_trade(conn, fill)

                side = fill.get("side", "").upper()
                symbol = fill.get("symbol", "")
                qty = float(fill.get("quantity", 0))
                price = float(fill.get("fill_price", 0))
                strategy = fill.get("strategy", "unknown")
                confidence = float(fill.get("confidence", 0))

                send_telegram_alert(
                    f"<b>{side}</b> {symbol}\n"
                    f"Qty: {qty:.6f}\n"
                    f"Price: {price:.4f}\n"
                    f"Strategy: {strategy}\n"
                    f"Confidence: {confidence:.2f}\n"
                    f"Live: {settings.live_trading}"
                )

            conn.execute(text("""
                INSERT INTO portfolio_snapshots(
                    equity, cash, exposure, drawdown, regime
                )
                VALUES(
                    :equity, :cash, :exposure, :drawdown, :regime
                )
            """), {
                "equity": equity,
                "cash": portfolio.cash,
                "exposure": exposure,
                "drawdown": portfolio.drawdown,
                "regime": regime,
            })

        equity_gauge.set(equity)
        drawdown_gauge.set(portfolio.drawdown)

        current_risk_status = "SAFE"
        exposure_pct = exposure / equity if equity else 0

        if portfolio.drawdown <= -0.05 or exposure_pct >= 0.50:
            current_risk_status = "BLOCKED"
        elif portfolio.drawdown <= -0.02 or exposure_pct >= 0.35:
            current_risk_status = "CAUTION"

        global_last = globals().get("last_risk_status")
        if current_risk_status != global_last:
            send_telegram_alert(
                f"Risk status changed: <b>{current_risk_status}</b>\n"
                f"Equity: {equity:.2f}\n"
                f"Exposure: {exposure:.2f}\n"
                f"Exposure %: {exposure_pct * 100:.2f}%\n"
                f"Drawdown: {portfolio.drawdown:.4f}"
            )
            globals()["last_risk_status"] = current_risk_status

        print({
            "regime": regime,
            "equity": round(equity, 2),
            "cash": round(portfolio.cash, 2),
            "exposure": round(exposure, 2),
            "exposure_pct": round(exposure / equity, 4) if equity else 0,
            "positions": {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0},
            "orders": len(applied_fills),
            "rejected": rejected[:3],
            "status": result["status"],
            "paused": is_paused(),
            "risk_status": current_risk_status,
        })

        time.sleep(settings.trade_interval_seconds)

    except Exception as e:
        print("Bot loop error:", str(e))
        time.sleep(5)
