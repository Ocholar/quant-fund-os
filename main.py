import time
import math
import statistics
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from datetime import datetime, timezone, timedelta
from core.config import settings
from core.db import engine
from core.portfolio import Portfolio
from core.regime import detect_regime
from core.risk_engine import RiskEngine
from data.ingestion import build_market_data
from data.feature_store import FeatureStore
from execution.executor import PaperExecutor, RealMEXCExecutor
from ai.autonomous_agent import AutonomousFundAgent
from services.metrics import trades_total, equity_gauge, drawdown_gauge
from core.control import is_paused, pause_bot, pause_reason
from services.telegram import send_telegram_alert

INITIAL_EQUITY = float(settings.starting_equity)

MAX_TOTAL_EXPOSURE_PCT = float(settings.max_total_exposure_pct)
MAX_SYMBOL_EXPOSURE_PCT = float(settings.max_symbol_exposure_pct)
MAX_TRADES_PER_SYMBOL = int(settings.max_trades_per_symbol)

# ============================================================
# ENTRY QUALITY LOCKDOWN
# Normal MEXC/FeatureStore features only. No raw fallback trading.
# Designed for small account + limited trade slots.
# ============================================================
ENTRY_QUALITY_LOCKDOWN_ENABLED = True
ENTRY_QUALITY_TOP_N = int(getattr(settings, "entry_quality_top_n", 2))
ENTRY_MIN_SIGNAL_SIDEWAYS = float(getattr(settings, "entry_min_signal_sideways", 0.025))
ENTRY_MIN_SIGNAL_TRENDING = float(getattr(settings, "entry_min_signal_trending", 0.018))
ENTRY_MAX_VOLATILITY = float(getattr(settings, "entry_max_volatility", 0.008))
ENTRY_MIN_EXPECTED_MOVE_PCT = float(getattr(settings, "entry_min_expected_move_pct", 0.012))
ENTRY_STOP_LOSS_QUARANTINE_HOURS = float(getattr(settings, "entry_stop_loss_quarantine_hours", 4))
ENTRY_REQUIRE_TRIPLE_AGREEMENT = str(getattr(settings, "entry_require_triple_agreement", "true")).lower() in ("1", "true", "yes", "on")
ENTRY_BLOCK_SIDEWAYS_IF_NO_TOP_CANDIDATE = True


# ============================================================
# SIDEWAYS ENTRY PACING + EXCEPTIONAL SIGNAL LADDER
# Normal SIDEWAYS entries are paced.
# Exceptional entries can bypass time quota, but not risk/quality.
# ============================================================

# ============================================================
# ENTRY CLEANUP / ANTI-CLUSTERING
# Prevent repeated same-symbol buys from consuming slots.
# ============================================================
SAME_SYMBOL_ENTRY_COOLDOWN_MINUTES = float(getattr(settings, "same_symbol_entry_cooldown_minutes", 30))
SAME_SYMBOL_EXCEPTIONAL_COOLDOWN_MINUTES = float(getattr(settings, "same_symbol_exceptional_cooldown_minutes", 10))
ENTRY_QUALITY_LOG_REJECTION_LIMIT = int(getattr(settings, "entry_quality_log_rejection_limit", 12))

SIDEWAYS_ENTRY_MIN_GAP_MINUTES = float(getattr(settings, "sideways_entry_min_gap_minutes", 15))
SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE = int(getattr(settings, "sideways_reserve_final_slot_until_minute", 35))
SIDEWAYS_EXCEPTIONAL_SIGNAL = float(getattr(settings, "sideways_exceptional_signal", 0.045))
SIDEWAYS_EXCEPTIONAL_LADDER = [
    float(x.strip())
    for x in str(getattr(settings, "sideways_exceptional_ladder", "0.045,0.050,0.055,0.060,0.065,0.070")).split(",")
    if x.strip()
]
SIDEWAYS_EXCEPTIONAL_BYPASS_HOURLY_CAP = str(getattr(settings, "sideways_exceptional_bypass_hourly_cap", "true")).lower() in ("1", "true", "yes", "on")
SIDEWAYS_EXCEPTIONAL_BYPASS_PACING = str(getattr(settings, "sideways_exceptional_bypass_pacing", "true")).lower() in ("1", "true", "yes", "on")
ENTRY_REQUIRE_LONG_TREND = str(getattr(settings, "entry_require_long_trend", "true")).lower() in ("1", "true", "yes", "on")

EXCLUDED_ENTRY_SYMBOLS = {
    "USDC/USDT",
    "USD1/USDT",
    "EUR/USDT",
    "GOLD(PAXG)/USDT",
}

# Count max_trades_per_symbol over a rolling window, not lifetime.
# This prevents old experiment trades from permanently blocking symbols.
TRADE_COUNT_WINDOW_HOURS = float(getattr(settings, "trade_count_window_hours", 4))
STOP_LOSS_PCT = float(settings.stop_loss_pct)
TAKE_PROFIT_PCT = float(settings.take_profit_pct)

# Single-exit profit system for small account / limited trade slots.
# No staged partial sells. Exit full position when the trade has done enough.
FULL_TAKE_PROFIT_PCT = float(getattr(settings, "full_take_profit_pct", 0.012))
BREAKEVEN_TRIGGER_PCT = float(getattr(settings, "breakeven_trigger_pct", 0.006))
BREAKEVEN_EXIT_PCT = float(getattr(settings, "breakeven_exit_pct", 0.001))
TIME_STOP_MINUTES = float(getattr(settings, "position_time_stop_minutes", 45))
TIME_STOP_EXIT_BELOW_PCT = float(getattr(settings, "time_stop_exit_below_pct", 0.003))
TAKE_PROFIT_SELL_FRACTION = float(settings.take_profit_sell_fraction)
DAILY_LOSS_LIMIT_PCT = float(settings.max_daily_loss)
COOLDOWN_SECONDS = int(settings.cooldown_seconds)
FEE_RATE = float(settings.trading_fee_rate)

MAX_DAILY_LOSS_PCT = float(settings.max_daily_loss)
SIDEWAYS_MAX_ENTRIES_PER_HOUR = int(settings.sideways_max_entries_per_hour)
SIDEWAYS_MIN_CONFIDENCE = float(settings.sideways_min_confidence)
TRENDING_MAX_ENTRIES_PER_HOUR = int(settings.trending_max_entries_per_hour)
TRENDING_MIN_CONFIDENCE = float(settings.trending_min_confidence)
LIQUIDITY_ERROR_LIMIT = 3
LIQUIDITY_ERROR_WINDOW_SECONDS = 600

liquidity_error_times = []
last_auto_pause_reason = None
last_known_equity = INITIAL_EQUITY
last_known_exposure = 0.0
last_known_regime = "UNKNOWN"
last_seen_paused_state = None

ALLOW_BUYS = True
ALLOW_SELLS = True

# Do not open new speculative trades on stable/quote-like symbols.
# These produced bad noise trades during the aggressive test.
EXCLUDED_TRADING_SYMBOLS = {
    "USDC/USDT",
    "USD1/USDT",
    "EUR/USDT",
    "GOLD(PAXG)/USDT",
}

quarantined_symbols = {}
quarantined_strategies = {}


portfolio = Portfolio(cash=INITIAL_EQUITY)
market = build_market_data(settings.symbol_list)
features = FeatureStore()
risk = RiskEngine()
if settings.live_trading:
    executor = RealMEXCExecutor()
else:
    executor = PaperExecutor()
agent = AutonomousFundAgent(risk, executor)

entry_prices = {}
trade_counts = {}
last_trade_time = {}
position_open_time = {}
position_peak_change = {}

shadow_positions = {}
shadow_entry_prices = {}
shadow_trade_counts = {}

def load_state_from_db():
    print("Recovering state from database...")
    try:
        with engine.begin() as conn:
            # 1. Recover portfolio cash and peak
            snap = conn.execute(text("""
                SELECT cash, equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1
            """)).mappings().first()
            if snap:
                recovered_cash = float(snap["cash"])
                recovered_equity = float(snap["equity"] or INITIAL_EQUITY)
                if recovered_cash < -0.01 or recovered_equity > INITIAL_EQUITY * 5:
                    msg = (
                        f"state_corruption_detected cash={recovered_cash:.2f} "
                        f"equity={recovered_equity:.2f}; reset quant.db before continuing"
                    )
                    print("WARNING:", msg)
                    pause_bot(msg)
                    portfolio.cash = max(0.0, min(recovered_cash, INITIAL_EQUITY))
                    portfolio.peak = INITIAL_EQUITY
                else:
                    portfolio.cash = recovered_cash
                    portfolio.peak = max(portfolio.peak, recovered_equity)
                print(f"Recovered cash: ${portfolio.cash:.2f}")

            # 2. Recover open positions
            rows = conn.execute(text("""
                SELECT symbol, quantity, avg_entry FROM positions WHERE quantity > 0
            """)).mappings().all()
            for r in rows:
                portfolio.positions[r["symbol"]] = float(r["quantity"])
                entry_prices[r["symbol"]] = float(r["avg_entry"])
            if rows:
                print(f"Recovered {len(rows)} open positions.")

            # 3. Recover recent trade metadata only.
            # Do not let old test trades permanently block a symbol.
            trades = conn.execute(text("""
                SELECT symbol, created_at
                FROM trades
                WHERE side = 'buy'
                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')
            """), {"hours": TRADE_COUNT_WINDOW_HOURS}).mappings().all()

            for t in trades:
                sym = t["symbol"]
                trade_counts[sym] = trade_counts.get(sym, 0) + 1

                try:
                    dt = datetime.fromisoformat(str(t["created_at"]))
                    ts = dt.timestamp()
                    last_trade_time[sym] = max(last_trade_time.get(sym, 0), ts)
                except:
                    pass
    except Exception as e:
        print(f"State recovery failed: {e}")

print("Quant Fund OS starting. LIVE_TRADING=", settings.live_trading)
print("Safety mode enabled. Paper trading only.")
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


def cleanup_expired_quarantines():
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM symbol_quarantine
                WHERE blocked_until IS NOT NULL
                  AND blocked_until <= DATETIME('now', '+3 hours')
            """))
    except Exception:
        pass


def can_buy(symbol, fill, prices, equity):
    cleanup_expired_quarantines()
    if not ALLOW_BUYS:
        return False, "buys_disabled"

    if symbol in EXCLUDED_TRADING_SYMBOLS:
        return False, "excluded_quote_or_stable_symbol"

    # SIDEWAYS governor:
    # During chop, do not allow too many simultaneous small positions.
    try:
        open_positions_count = sum(
            1 for _, q in portfolio.positions.items()
            if float(q or 0) > 0.00000001
        )

        current_total_exposure = total_exposure(prices)
        current_exposure_pct = current_total_exposure / max(float(equity or 0), 0.000001)

        if str(last_known_regime or "").upper() == "SIDEWAYS":
            if open_positions_count >= 8:
                return False, f"sideways_max_open_positions_{open_positions_count}"
            if current_exposure_pct >= 0.15:
                return False, f"sideways_max_exposure_{current_exposure_pct:.4f}"
    except Exception:
        pass

    # Reduce trading while account is in CAUTION/drawdown.
    try:
        current_drawdown = float(getattr(portfolio, "drawdown", 0.0) or 0.0)
        caution_drawdown = float(getattr(settings, "caution_drawdown", -0.02))
        blocked_drawdown = float(getattr(settings, "blocked_drawdown", -0.05))

        if current_drawdown <= blocked_drawdown * 0.90:
            return False, f"near_blocked_drawdown_{current_drawdown:.4f}"

        if current_drawdown <= caution_drawdown:
            open_positions_count = sum(
                1 for _, q in portfolio.positions.items()
                if float(q or 0) > 0.00000001
            )

            try:
                current_exposure = float(getattr(portfolio, "exposure", 0.0) or 0.0)
                current_exposure_pct = current_exposure / max(float(equity or 0.0), 0.000001)
            except Exception:
                current_exposure_pct = 0.0

            # In CAUTION, do not freeze tiny positions.
            # Block only when exposure is already meaningful or too many symbols are open.
            if current_exposure_pct >= 0.20:
                return False, f"caution_mode_exposure_{current_exposure_pct:.4f}"

            if open_positions_count >= 10:
                return False, f"caution_mode_max_positions_{open_positions_count}"
    except Exception:
        pass

    # Avoid symbols where historical stop-loss exits exceed take-profit exits.
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    SUM(CASE WHEN strategy='stop_loss' THEN 1 ELSE 0 END) AS stop_losses,
                    SUM(CASE WHEN strategy='take_profit' THEN 1 ELSE 0 END) AS take_profits
                FROM trades
                WHERE symbol = :symbol
                  AND side = 'sell'
                  AND created_at >= DATETIME('now', '+3 hours', '-3 hours')
            """), {"symbol": symbol}).mappings().first()

            if row:
                stop_losses = int(row["stop_losses"] or 0)
                take_profits = int(row["take_profits"] or 0)

                if stop_losses >= 3 and stop_losses >= (take_profits + 2):
                    return False, f"symbol_bad_history_sl{stop_losses}_tp{take_profits}"
    except Exception:
        pass

    if portfolio.positions.get(symbol, 0.0) > 0.00000001:
        return False, "already_holding_symbol"

    if symbol in quarantined_symbols and time.time() < quarantined_symbols[symbol]:
        return False, "symbol_quarantined"

    if equity <= INITIAL_EQUITY * (1 - DAILY_LOSS_LIMIT_PCT):
        return False, "daily_loss_limit"

    now = time.time()

    if now - last_trade_time.get(symbol, 0) < COOLDOWN_SECONDS:
        return False, "cooldown"

    recent_symbol_trades = recent_symbol_buy_count(symbol)
    trade_counts[symbol] = recent_symbol_trades

    if recent_symbol_trades >= MAX_TRADES_PER_SYMBOL:
        return False, f"max_trades_per_symbol_recent_{recent_symbol_trades}_in_{TRADE_COUNT_WINDOW_HOURS:g}h"

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
    fee = (qty * price) * FEE_RATE # fee model from settings
    cost = (qty * price) + fee

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

    fee = (sell_qty * price) * FEE_RATE
    portfolio.cash += (sell_qty * price) - fee
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

def apply_shadow_buy(fill):
    symbol = fill["symbol"]
    qty = float(fill["quantity"])
    price = float(fill["fill_price"])
    strategy = fill.get("strategy", "unknown")

    old_qty = shadow_positions.get(symbol, 0.0)
    old_avg = shadow_entry_prices.get(symbol, price)

    new_qty = old_qty + qty
    new_avg = ((old_qty * old_avg) + (qty * price)) / new_qty

    shadow_positions[symbol] = new_qty
    shadow_entry_prices[symbol] = new_avg
    shadow_trade_counts[symbol] = shadow_trade_counts.get(symbol, 0) + 1
    return True

def apply_shadow_sell(symbol, qty, price, reason):
    held = shadow_positions.get(symbol, 0.0)
    sell_qty = min(qty, held)

    if sell_qty <= 0:
        return None

    shadow_positions[symbol] = held - sell_qty
    if shadow_positions[symbol] <= 0.00000001:
        shadow_positions[symbol] = 0.0
        shadow_entry_prices.pop(symbol, None)

    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": sell_qty,
        "expected_price": price,
        "fill_price": price,
        "slippage_bps": 0,
        "strategy": reason,
        "confidence": 1.0,
        "shadow_mode": True
    }



def adaptive_exit_thresholds(symbol, regime):
    """
    Dynamic stop-loss / take-profit.

    Goal:
    - Avoid tiny fixed stops that get hit by normal crypto noise.
    - Keep risk bounded.
    - Demand better reward than risk.
    - Be stricter in SIDEWAYS but not frozen.

    Uses PRICE_HISTORY if available; otherwise falls back to settings.
    """
    try:
        history = globals().get("PRICE_HISTORY", {}).get(symbol, [])
        returns = []

        for i in range(1, len(history)):
            prev = float(history[i - 1])
            now = float(history[i])
            if prev > 0:
                returns.append((now - prev) / prev)

        if len(returns) >= 5:
            vol = statistics.pstdev(returns)
        else:
            vol = float(STOP_LOSS_PCT) / 2
    except Exception:
        vol = float(STOP_LOSS_PCT) / 2

    r = str(regime or "").upper()

    base_stop = float(STOP_LOSS_PCT)
    base_take = float(TAKE_PROFIT_PCT)

    if r == "SIDEWAYS":
        # Wider than fixed stop to avoid chop, but not reckless.
        stop = max(base_stop, vol * 2.5, 0.012)
        take = max(base_take, stop * 1.8, 0.020)
    elif r == "RISK_OFF":
        stop = max(base_stop * 0.75, vol * 1.8, 0.008)
        take = max(base_take, stop * 1.5)
    else:
        stop = max(base_stop, vol * 2.2, 0.010)
        take = max(base_take, stop * 2.0, 0.022)

    # Hard bounds for a small $100 paper account.
    stop = min(stop, 0.035)
    take = min(take, 0.080)

    return stop, take



def get_position_age_minutes(symbol):
    now = time.time()

    if symbol not in position_open_time:
        position_open_time[symbol] = now

    return (now - position_open_time[symbol]) / 60.0


def update_position_peak_change(symbol, change):
    old_peak = position_peak_change.get(symbol)

    if old_peak is None:
        position_peak_change[symbol] = change
    else:
        position_peak_change[symbol] = max(float(old_peak), float(change))

    return float(position_peak_change.get(symbol, change))


def clear_position_exit_trackers(symbol):
    position_open_time.pop(symbol, None)
    position_peak_change.pop(symbol, None)
    position_open_time.pop("shadow_" + symbol, None)
    position_peak_change.pop("shadow_" + symbol, None)


def generate_sells(prices, regime):
    sells = []

    if not ALLOW_SELLS:
        return sells

    for symbol, qty in list(portfolio.positions.items()):
        if qty <= 0.0001:
            continue

        price = prices.get(symbol)
        avg_entry = entry_prices.get(symbol)

        if not price or not avg_entry:
            continue

        change = (price - avg_entry) / avg_entry
        dynamic_stop_pct, dynamic_take_pct = adaptive_exit_thresholds(symbol, regime)

        if change <= -dynamic_stop_pct:
            sell = apply_sell(symbol, qty, price, "adaptive_stop_loss")
            if sell:
                sells.append(sell)
                quarantined_symbols[symbol] = time.time() + 86400 # Block for 24h
                quarantine_symbol(symbol, "stop_loss_exit", hours=1)

        elif change >= dynamic_take_pct:
            sell = apply_sell(symbol, qty * TAKE_PROFIT_SELL_FRACTION, price, "adaptive_take_profit")
            if sell:
                sells.append(sell)

        elif regime == "RISK_OFF":
            sell = apply_sell(symbol, qty, price, "risk_off_exit")
            if sell:
                sells.append(sell)

    # Shadow sells
    for symbol, qty in list(shadow_positions.items()):
        if qty <= 0:
            continue

        price = prices.get(symbol)
        avg_entry = shadow_entry_prices.get(symbol)

        if not price or not avg_entry:
            continue

        change = (price - avg_entry) / avg_entry
        dynamic_stop_pct, dynamic_take_pct = adaptive_exit_thresholds(symbol, regime)

        if change <= -dynamic_stop_pct:
            sell = apply_shadow_sell(symbol, qty, price, "adaptive_stop_loss")
            if sell:
                sells.append(sell)
        elif change >= dynamic_take_pct:
            sell = apply_shadow_sell(symbol, qty * TAKE_PROFIT_SELL_FRACTION, price, "adaptive_take_profit")
            if sell:
                sells.append(sell)
        elif regime == "RISK_OFF":
            sell = apply_shadow_sell(symbol, qty, price, "risk_off_exit")
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
            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
        )
        VALUES(
            :symbol, :side, :quantity, :expected_price, :fill_price,
            :slippage_bps, 0, :strategy, :confidence, :live, :shadow_mode, DATETIME('now', '+3 hours')
        )
    """), fill | {"live": settings.live_trading, "shadow_mode": fill.get("shadow_mode", False)})

def ensure_positions_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                quantity REAL NOT NULL DEFAULT 0,
                avg_entry REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                last_price REAL NOT NULL DEFAULT 0,
                exposure REAL NOT NULL DEFAULT 0,
                strategy TEXT,
                updated_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))
            )
        """))
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(positions)"))]
        if "strategy" not in cols:
            conn.execute(text("ALTER TABLE positions ADD COLUMN strategy TEXT"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS symbol_quarantine (
                symbol TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                blocked_until DATETIME,
                created_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))
            )
        """))

def quarantine_symbol(symbol: str, reason: str, hours: int = 24):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO symbol_quarantine (symbol, reason, blocked_until, created_at)
            VALUES (:symbol, :reason, datetime('now', '+' || :hours || ' hours', '+3 hours'), datetime('now', '+3 hours'))
            ON CONFLICT (symbol) DO UPDATE SET
            reason = EXCLUDED.reason,
            blocked_until = EXCLUDED.blocked_until,
            created_at = EXCLUDED.created_at
        """), {"symbol": symbol, "reason": reason, "hours": hours})
    send_telegram_alert(f"<b>Symbol Quarantined</b>\nSymbol: {symbol}\nReason: {reason}")


def update_position_from_fill(conn, fill):
    symbol = fill["symbol"]
    side = fill["side"]
    qty = float(fill["quantity"])
    price = float(fill["fill_price"])

    existing = conn.execute(text("""
        SELECT symbol, quantity, avg_entry, realized_pnl, strategy
        FROM positions
        WHERE symbol = :symbol
    """), {"symbol": symbol}).mappings().first()

    if not existing:
        existing_qty = 0.0
        avg_entry = 0.0
        realized_pnl = 0.0
    else:
        existing_qty = float(existing["quantity"] or 0)
        avg_entry = float(existing["avg_entry"] or 0)
        realized_pnl = float(existing["realized_pnl"] or 0)
        existing_strategy = existing["strategy"] or "unknown"

    if side == "buy":
        new_qty = existing_qty + qty
        new_strategy = fill.get("strategy", existing_strategy if existing else "unknown")
        fee_adjusted_price = price * (1 + FEE_RATE)
        if new_qty > 0:
            new_avg_entry = ((existing_qty * avg_entry) + (qty * fee_adjusted_price)) / new_qty
        else:
            new_avg_entry = 0.0

        new_realized_pnl = realized_pnl
        fill_pnl = 0.0
        applied_strategy = new_strategy

    elif side == "sell":
        sell_qty = min(qty, existing_qty)
        net_sell_price = price * (1 - FEE_RATE)
        fill_pnl = sell_qty * (net_sell_price - avg_entry)

        new_qty = max(existing_qty - sell_qty, 0.0)
        new_avg_entry = avg_entry if new_qty > 0 else 0.0
        new_realized_pnl = realized_pnl + fill_pnl
        new_strategy = existing_strategy
        applied_strategy = existing_strategy

        if new_realized_pnl <= -2.0:
            quarantine_symbol(symbol, f"realized_pnl_exceeded_limit_{new_realized_pnl:.2f}")

    else:
        return 0.0

    exposure = new_qty * price
    unrealized_pnl = new_qty * (price - new_avg_entry) if new_qty > 0 else 0.0

    conn.execute(text("""
        INSERT INTO positions(
            symbol, quantity, avg_entry, realized_pnl,
            unrealized_pnl, last_price, exposure, strategy, updated_at
        )
        VALUES(
            :symbol, :quantity, :avg_entry, :realized_pnl,
            :unrealized_pnl, :last_price, :exposure, :strategy, DATETIME('now', '+3 hours')
        )
        ON CONFLICT (symbol)
        DO UPDATE SET
            quantity = EXCLUDED.quantity,
            avg_entry = EXCLUDED.avg_entry,
            realized_pnl = EXCLUDED.realized_pnl,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            last_price = EXCLUDED.last_price,
            exposure = EXCLUDED.exposure,
            strategy = EXCLUDED.strategy,
            updated_at = DATETIME('now', '+3 hours')
    """), {
        "symbol": symbol,
        "quantity": new_qty,
        "avg_entry": new_avg_entry,
        "realized_pnl": new_realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "last_price": price,
        "exposure": exposure,
        "strategy": new_strategy
    })

    return fill_pnl, applied_strategy


def mark_positions_to_market(conn, prices):
    for symbol, price in prices.items():
        row = conn.execute(text("""
            SELECT quantity, avg_entry, realized_pnl
            FROM positions
            WHERE symbol = :symbol
        """), {"symbol": symbol}).mappings().first()

        if not row:
            continue

        qty = float(row["quantity"] or 0)
        avg_entry = float(row["avg_entry"] or 0)

        exposure = qty * float(price)
        unrealized_pnl = qty * (float(price) - avg_entry) if qty > 0 else 0.0

        conn.execute(text("""
            UPDATE positions
            SET last_price = :last_price,
                exposure = :exposure,
                unrealized_pnl = :unrealized_pnl,
                updated_at = DATETIME('now', '+3 hours')
            WHERE symbol = :symbol
        """), {
            "symbol": symbol,
            "last_price": float(price),
            "exposure": exposure,
            "unrealized_pnl": unrealized_pnl,
        })

wait_for_database()
ensure_positions_table()

def send_auto_pause(reason: str, equity: float, exposure: float, regime: str):
    global last_auto_pause_reason

    if last_auto_pause_reason == reason:
        return

    last_auto_pause_reason = reason
    pause_bot(reason)

    send_telegram_alert(
        f"<b>Quant Fund OS AUTO-PAUSED</b>\n"
        f"Reason: {reason}\n"
        f"Equity: {equity:.2f}\n"
        f"Exposure: {exposure:.2f}\n"
        f"Regime: {regime}\n"
        f"Live trading: {settings.live_trading}"
    )


def get_day_start_equity(default_equity: float = 100.0):
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT equity
            FROM portfolio_snapshots
            WHERE created_at >= date('now')
            ORDER BY id ASC
            LIMIT 1
        """)).mappings().first()

    if not row:
        return default_equity

    return float(row["equity"] or default_equity)


def check_daily_loss_guard(equity: float, exposure: float, regime: str):
    day_start_equity = get_day_start_equity(INITIAL_EQUITY)

    if day_start_equity <= 0:
        return False

    daily_pnl_pct = (equity - day_start_equity) / day_start_equity

    if daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
        send_auto_pause(
            f"max_daily_loss_hit_{daily_pnl_pct:.2%}",
            equity,
            exposure,
            regime,
        )
        return True

    return False


def recent_buy_count():
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT COUNT(*) AS count
            FROM trades
            WHERE side = 'buy'
              AND created_at >= datetime('now', '+3 hours', '-1 hour')
        """)).mappings().first()

    return int(row["count"] or 0) if row else 0



def recent_symbol_buy_count(symbol: str):
    """
    Count recent buys for a symbol in a rolling window.
    This replaces lifetime trade_counts blocking.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS count
                FROM trades
                WHERE symbol = :symbol
                  AND side = 'buy'
                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')
            """), {
                "symbol": symbol,
                "hours": TRADE_COUNT_WINDOW_HOURS,
            }).mappings().first()

        return int(row["count"] or 0) if row else 0
    except Exception:
        return int(trade_counts.get(symbol, 0) or 0)


def refresh_recent_trade_counts():
    """
    Rebuild in-memory trade_counts from only recent buy trades.
    Keeps diagnostics and can_buy aligned with the rolling window.
    """
    global trade_counts
    trade_counts = {}

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT symbol, COUNT(*) AS count
                FROM trades
                WHERE side = 'buy'
                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')
                GROUP BY symbol
            """), {
                "hours": TRADE_COUNT_WINDOW_HOURS,
            }).mappings().all()

        for r in rows:
            trade_counts[r["symbol"]] = int(r["count"] or 0)

    except Exception as e:
        print("TRADE_COUNT_REFRESH_ERROR:", e)


def is_trending_regime(regime: str):
    r = str(regime or "").upper()
    return r in {"BULL", "BULLISH", "TRENDING", "UPTREND", "TREND"}


def entry_policy_allows(symbol: str, regime: str, confidence: float, entries_this_cycle: int, strategy: str = None):
    with engine.begin() as conn:
        q = conn.execute(text("SELECT symbol FROM symbol_quarantine WHERE symbol = :sym AND blocked_until IS NOT NULL AND blocked_until > DATETIME('now', '+3 hours')"), {"sym": symbol}).first()
        if q:
            return False, "symbol_quarantined"

        if strategy:
            s_score = conn.execute(text("SELECT status FROM strategy_scores WHERE strategy = :s"), {"s": strategy}).mappings().first()
            if s_score and s_score["status"] == "blocked":
                return False, f"strategy_{strategy}_blocked"

    r = str(regime or "").upper()

    if r == "RISK_OFF":
        return False, "risk_off_blocks_new_buys"

    recent_entries = recent_buy_count() + entries_this_cycle

    if r == "SIDEWAYS":
        if confidence < SIDEWAYS_MIN_CONFIDENCE:
            return False, f"sideways_confidence_too_low_{confidence:.2f}"
        if recent_entries >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
            return False, "sideways_max_entries_per_hour_hit"
        return True, "ok"

    if is_trending_regime(r):
        if confidence < TRENDING_MIN_CONFIDENCE:
            return False, f"trending_confidence_too_low_{confidence:.2f}"
        if recent_entries >= TRENDING_MAX_ENTRIES_PER_HOUR:
            return False, "trending_max_entries_per_hour_hit"
        return True, "ok"

    if confidence < SIDEWAYS_MIN_CONFIDENCE:
        return False, f"default_confidence_too_low_{confidence:.2f}"

    if recent_entries >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
        return False, "default_max_entries_per_hour_hit"

    return True, "ok"

def reset_liquidity_errors():
    global liquidity_error_times
    liquidity_error_times = []

def register_liquidity_error(error_message: str):
    global liquidity_error_times

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LIQUIDITY_ERROR_WINDOW_SECONDS)

    liquidity_error_times = [
        t for t in liquidity_error_times
        if t >= cutoff
    ]

    liquidity_error_times.append(now)

    if len(liquidity_error_times) >= LIQUIDITY_ERROR_LIMIT:
        send_auto_pause(
            "liquidity_error_circuit_breaker",
            0.0,
            0.0,
            "UNKNOWN",
        )

def final_trade_firewall(fill, regime):
    side = str(fill.get("side", "")).lower()
    strategy = str(fill.get("strategy", ""))
    confidence = float(fill.get("confidence", 0))

    # Sells are always allowed for safety exits.
    if side == "sell":
        return True, "sell_allowed"

    # Only buys need strict gating.
    if side != "buy":
        return False, "unknown_side_blocked"

    r = str(regime or "").upper()

    if r == "RISK_OFF":
        return False, "risk_off_blocks_buy"

    if r == "SIDEWAYS" and confidence < SIDEWAYS_MIN_CONFIDENCE:
        return False, f"sideways_confidence_too_low_{confidence:.2f}"
    
    if is_trending_regime(r) and confidence < TRENDING_MIN_CONFIDENCE:
        return False, f"trending_confidence_too_low_{confidence:.2f}"

    # Unknown regimes should be conservative.
    if r not in {"SIDEWAYS", "BULL", "BULLISH", "TRENDING", "UPTREND", "TREND"}:
        return False, f"unknown_regime_blocks_buy_{r}"

    return True, "buy_allowed"



# ============================================================
# QUANT FUND OS — DIAGNOSTIC + FALLBACK SIGNAL PATCH
# ============================================================

PRICE_HISTORY = {}
EMPTY_FEATURE_CYCLES = 0


def remember_prices(prices, max_len=30):
    global PRICE_HISTORY

    if not isinstance(prices, dict):
        return

    for symbol, price in prices.items():
        try:
            price = float(price)
            PRICE_HISTORY.setdefault(symbol, []).append(price)
            if len(PRICE_HISTORY[symbol]) > max_len:
                PRICE_HISTORY[symbol] = PRICE_HISTORY[symbol][-max_len:]
        except Exception:
            continue


def build_raw_momentum_fallback(prices, min_history=5):
    fallback_features = {}

    if not isinstance(prices, dict):
        return fallback_features

    for symbol, history in PRICE_HISTORY.items():
        try:
            if len(history) < min_history:
                continue

            current_price = float(history[-1])
            old_price = float(history[-min_history])

            if old_price <= 0:
                continue

            momentum = (current_price - old_price) / old_price

            returns = []
            for i in range(1, len(history)):
                prev = float(history[i - 1])
                now = float(history[i])
                if prev > 0:
                    returns.append((now - prev) / prev)

            volatility = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
            abs_momentum = abs(momentum)

            # Ignore completely flat/noisy symbols.
            if abs_momentum < 0.0015:
                continue

            direction = "BUY" if momentum > 0 else "SELL"
            signal_strength = min(1.0, abs_momentum * 80 + volatility * 20)

            fallback_features[symbol] = {
                "ready": True,
                "source": "RAW_MOMENTUM_FALLBACK",
                "direction": direction,
                "price": current_price,
                "trend": momentum,
                "long_trend": momentum,
                "momentum": momentum,
                "one_tick_momentum": momentum,
                "volatility": volatility,
                "signal_strength": signal_strength,
                "confidence": signal_strength,
                "reason": "normal_features_empty"
            }

        except Exception:
            continue

    ranked = sorted(
        fallback_features.items(),
        key=lambda item: item[1].get("signal_strength", 0),
        reverse=True
    )

    return dict(ranked[:5])


def build_fallback_orders(fallback_features, prices, equity, cash, regime, max_orders=2):
    orders = []

    if not isinstance(fallback_features, dict):
        return orders

    ranked = sorted(
        fallback_features.items(),
        key=lambda item: item[1].get("signal_strength", 0),
        reverse=True
    )

    for symbol, data in ranked:
        if len(orders) >= max_orders:
            break

        if not isinstance(data, dict):
            continue

        # For now, fallback only opens BUY entries.
        # Sells are still handled by generate_sells().
        if data.get("direction") != "BUY":
            continue

        price = float(prices.get(symbol, 0) or data.get("price", 0) or 0)
        if price <= 0:
            continue

        confidence = float(data.get("confidence", data.get("signal_strength", 0)) or 0)

        # Make fallback compatible with current entry policy thresholds,
        # while still allowing weak signals to be rejected later.
        r = str(regime or "").upper()
        if r == "SIDEWAYS":
            confidence = max(confidence, float(SIDEWAYS_MIN_CONFIDENCE) + 0.01)
        elif is_trending_regime(r):
            confidence = max(confidence, float(TRENDING_MIN_CONFIDENCE) + 0.01)

        confidence = min(confidence, 0.95)

        # Small but real starter position for a $100 account.
        # can_buy() and final_trade_firewall() will still enforce exposure/risk rules.
        position_value = min(
            max(float(equity or 0) * 0.025, 1.0),
            float(cash or 0) * 0.05
        )

        if position_value <= 0:
            continue

        qty = position_value / price

        orders.append({
            "symbol": symbol,
            "side": "buy",
            "quantity": qty,
            "expected_price": price,
            "fill_price": price,
            "slippage_bps": 0,
            "strategy": "raw_momentum_fallback",
            "confidence": confidence,
        })

    return orders


def log_cycle_diagnostic(market_data=None, features=None, orders=None, portfolio=None, rejected=None, note=""):
    global EMPTY_FEATURE_CYCLES

    market_count = len(market_data) if isinstance(market_data, dict) else 0
    feature_count = len(features) if isinstance(features, dict) else 0
    order_count = len(orders) if isinstance(orders, list) else 0

    if feature_count == 0:
        EMPTY_FEATURE_CYCLES += 1
    else:
        EMPTY_FEATURE_CYCLES = 0

    if market_count == 0:
        no_trade_reason = "no_market_data"
    elif feature_count == 0:
        no_trade_reason = "features_empty"
    elif order_count == 0:
        no_trade_reason = "features_exist_but_no_orders"
    else:
        no_trade_reason = "orders_created_or_applied"

    print("\n" + "=" * 72)
    print("QUANT FUND OS — CYCLE DIAGNOSTIC")
    print(f"Time: {datetime.utcnow().isoformat()}Z")
    print(f"Market symbols: {market_count}")
    print(f"Feature symbols: {feature_count}")
    print(f"Orders/applied fills: {order_count}")
    print(f"Empty feature cycles: {EMPTY_FEATURE_CYCLES}")
    print(f"No-trade reason: {no_trade_reason}")

    if isinstance(portfolio, dict):
        print(f"Regime: {portfolio.get('regime')}")
        print(f"Risk: {portfolio.get('risk_status')}")
        print(f"Equity: {portfolio.get('equity')}")
        print(f"Cash: {portfolio.get('cash')}")
        print(f"Exposure pct: {portfolio.get('exposure_pct')}")
        positions = portfolio.get("positions") or {}
        print(f"Open positions: {len(positions) if isinstance(positions, dict) else 0}")

    if note:
        print(f"Note: {note}")

    if isinstance(features, dict) and features:
        ranked = sorted(
            features.items(),
            key=lambda item: item[1].get("signal_strength", item[1].get("confidence", 0))
            if isinstance(item[1], dict) else 0,
            reverse=True
        )
        print("Top features/signals:")
        for symbol, data in ranked[:5]:
            if isinstance(data, dict):
                print(
                    f"  {symbol} | source={data.get('source', 'NORMAL')} "
                    f"direction={data.get('direction')} "
                    f"strength={round(float(data.get('signal_strength', 0)), 4)} "
                    f"momentum={round(float(data.get('momentum', 0)), 5)}"
                )

    if rejected:
        print(f"Rejected sample: {rejected[:5]}")

    print("=" * 72 + "\n")


def _feature_value(data, key, default=0.0):
    try:
        if isinstance(data, dict):
            return float(data.get(key, default) or 0.0)
    except Exception:
        pass
    return float(default or 0.0)


def _entry_signal_threshold(regime):
    r = str(regime or "").upper()
    if r == "SIDEWAYS":
        return ENTRY_MIN_SIGNAL_SIDEWAYS
    return ENTRY_MIN_SIGNAL_TRENDING


def _is_normal_feature(data):
    """
    Raw momentum fallback must never be tradable.
    Missing source is treated as NORMAL because current FeatureStore
    often emits feature dicts without a source field.
    """
    if not isinstance(data, dict):
        return False
    source = str(data.get("source", "NORMAL")).upper()
    return source != "RAW_MOMENTUM_FALLBACK"


def _bullish_triple_agreement(data):
    trend = _feature_value(data, "trend")
    momentum = _feature_value(data, "momentum")
    one_tick = _feature_value(data, "one_tick_momentum")

    if ENTRY_REQUIRE_TRIPLE_AGREEMENT:
        return trend > 0 and momentum > 0 and one_tick > 0

    # fallback: require at least two bullish components
    return sum([trend > 0, momentum > 0, one_tick > 0]) >= 2


def _recent_stop_loss_blocked(symbol):
    """
    Extra stop-loss quarantine layer. This does not replace your existing
    symbol_quarantine table; it adds a rolling DB check so recently failed
    symbols cannot immediately consume slots again.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS count
                FROM trades
                WHERE symbol = :symbol
                  AND (
                        strategy IN ('stop_loss', 'adaptive_stop_loss')
                     OR side = 'sell' AND strategy LIKE '%stop_loss%'
                  )
                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')
            """), {
                "symbol": symbol,
                "hours": ENTRY_STOP_LOSS_QUARANTINE_HOURS,
            }).mappings().first()

        return int(row["count"] or 0) > 0
    except Exception as e:
        print(f"ENTRY QUALITY quarantine check error for {symbol}: {e}")
        return False


def _entry_quality_reason(symbol, data, regime):
    """
    Returns None if allowed by quality gate, otherwise a rejection reason.
    """
    if symbol in EXCLUDED_ENTRY_SYMBOLS:
        return "excluded_quote_or_stable_symbol"

    if not isinstance(data, dict) or not data.get("ready"):
        return "feature_not_ready"

    if not _is_normal_feature(data):
        return "raw_momentum_fallback_disabled"

    if _recent_stop_loss_blocked(symbol):
        return f"recent_stop_loss_quarantine_{ENTRY_STOP_LOSS_QUARANTINE_HOURS:g}h"

    signal_strength = _feature_value(data, "signal_strength")
    min_signal = _entry_signal_threshold(regime)

    if signal_strength < min_signal:
        return f"signal_too_weak_{signal_strength:.4f}_lt_{min_signal:.4f}"

    if ENTRY_REQUIRE_LONG_TREND:
        long_trend = _feature_value(data, "long_trend")
        if long_trend <= 0:
            return f"long_trend_not_positive_{long_trend:.4f}"

    if not _bullish_triple_agreement(data):
        trend = _feature_value(data, "trend")
        momentum = _feature_value(data, "momentum")
        one_tick = _feature_value(data, "one_tick_momentum")
        return f"triple_agreement_failed_t={trend:.4f}_m={momentum:.4f}_ot={one_tick:.4f}"

    volatility = abs(_feature_value(data, "volatility"))
    if volatility > ENTRY_MAX_VOLATILITY:
        return f"volatility_too_high_{volatility:.4f}_gt_{ENTRY_MAX_VOLATILITY:.4f}"

    expected_move = max(
        float(globals().get("FULL_TAKE_PROFIT_PCT", 0.0) or 0.0),
        float(globals().get("TAKE_PROFIT_PCT", 0.0) or 0.0),
    )
    if expected_move < ENTRY_MIN_EXPECTED_MOVE_PCT:
        return f"expected_move_too_small_{expected_move:.4f}_lt_{ENTRY_MIN_EXPECTED_MOVE_PCT:.4f}"

    return None


def entry_quality_ranked_symbols(feature_map, regime):
    """
    Build the eligible top-N execution list from normal FeatureStore features.
    This is the core ranking gate.
    """
    eligible = []
    rejected_preview = []

    if not isinstance(feature_map, dict):
        return set(), [], []

    for symbol, data in feature_map.items():
        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            rejected_preview.append({
                "symbol": symbol,
                "reason": f"entry_quality_{reason}",
            })
            continue

        eligible.append((
            symbol,
            _feature_value(data, "signal_strength"),
            _feature_value(data, "momentum"),
        ))

    eligible.sort(key=lambda x: (x[1], x[2]), reverse=True)

    top = eligible[:ENTRY_QUALITY_TOP_N]
    top_symbols = {s for s, _, _ in top}

    return top_symbols, top, rejected_preview



def _sideways_exceptional_level(signal_strength):
    """
    Returns 0 for non-exceptional.
    Returns 1..N for ladder level.
    """
    try:
        s = float(signal_strength or 0.0)
    except Exception:
        s = 0.0

    level = 0
    for threshold in sorted(SIDEWAYS_EXCEPTIONAL_LADDER):
        if s >= threshold:
            level += 1

    return level


def _is_exceptional_sideways_signal(data):
    if not isinstance(data, dict):
        return False

    return _sideways_exceptional_level(_feature_value(data, "signal_strength")) > 0


def _current_hour_minute_utc():
    try:
        return datetime.utcnow().minute
    except Exception:
        return 0


def _recent_buy_rows_for_sideways():
    """
    Reads recent buy entries from the trades table.
    Used only for pacing decisions.
    """
    rows = []
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT symbol, strategy, created_at
                FROM trades
                WHERE side = 'buy'
                  AND created_at >= datetime('now', '+3 hours', '-1 hours')
                ORDER BY created_at DESC
            """)).mappings().all()
    except Exception as e:
        print("SIDEWAYS PACING recent buy query error:", e)

    return list(rows or [])


def _minutes_since_last_sideways_entry():
    try:
        rows = _recent_buy_rows_for_sideways()
        if not rows:
            return 9999.0

        latest = rows[0].get("created_at")
        if not latest:
            return 9999.0

        latest_s = str(latest).replace("T", " ").split(".")[0]
        dt = datetime.fromisoformat(latest_s)
        age = (datetime.utcnow() - dt).total_seconds() / 60.0

        # If DB timestamps are local while datetime.utcnow is UTC, avoid negative lockups.
        if age < 0:
            age = abs(age)

        return age
    except Exception as e:
        print("SIDEWAYS PACING age error:", e)
        return 9999.0


def _sideways_recent_entry_count():
    try:
        return len(_recent_buy_rows_for_sideways())
    except Exception:
        return 0


def _sideways_pacing_reason(symbol, data, regime):
    """
    Returns None if this buy may proceed.
    Returns rejection reason if SIDEWAYS pacing blocks it.

    Exceptional signals can bypass hourly cap and pacing, but only after
    all normal entry-quality gates have already passed.
    """
    if str(regime or "").upper() != "SIDEWAYS":
        return None

    signal_strength = _feature_value(data, "signal_strength")
    exceptional_level = _sideways_exceptional_level(signal_strength)
    is_exceptional = exceptional_level > 0

    recent_count = _sideways_recent_entry_count()
    minutes_since_last = _minutes_since_last_sideways_entry()
    current_minute = _current_hour_minute_utc()

    if is_exceptional:
        print(
            f"SIDEWAYS EXCEPTIONAL SIGNAL: {symbol} "
            f"strength={signal_strength:.4f} level={exceptional_level} "
            f"recent_count={recent_count} minutes_since_last={minutes_since_last:.1f}"
        )

    # Normal entries obey hourly cap.
    if recent_count >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
        if not (is_exceptional and SIDEWAYS_EXCEPTIONAL_BYPASS_HOURLY_CAP):
            return f"sideways_hourly_cap_hit_{recent_count}_of_{SIDEWAYS_MAX_ENTRIES_PER_HOUR}"

    # Normal entries obey pacing.
    if minutes_since_last < SIDEWAYS_ENTRY_MIN_GAP_MINUTES:
        if not (is_exceptional and SIDEWAYS_EXCEPTIONAL_BYPASS_PACING):
            return f"sideways_pacing_wait_{minutes_since_last:.1f}_lt_{SIDEWAYS_ENTRY_MIN_GAP_MINUTES:g}_minutes"

    # Reserve last normal slot until later in hour.
    # Example: if max=3, do not use 3rd normal slot before minute 35.
    if recent_count >= max(0, SIDEWAYS_MAX_ENTRIES_PER_HOUR - 1):
        if current_minute < SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE:
            if not is_exceptional:
                return f"sideways_final_slot_reserved_until_minute_{SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE}"

    return None


def _minutes_since_symbol_buy(symbol):
    """
    Returns minutes since this symbol was last bought.
    Used to stop repeated same-symbol clustering.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT created_at
                FROM trades
                WHERE LOWER(side) = 'buy'
                  AND symbol = :symbol
                ORDER BY created_at DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

        if not row or not row.get("created_at"):
            return 9999.0

        latest_s = str(row.get("created_at")).replace("T", " ").split(".")[0]
        dt = datetime.fromisoformat(latest_s)
        age = (datetime.utcnow() - dt).total_seconds() / 60.0

        if age < 0:
            age = abs(age)

        return age

    except Exception as e:
        print(f"SAME SYMBOL COOLDOWN check error for {symbol}: {e}")
        return 9999.0


def _same_symbol_cooldown_reason(symbol, data):
    """
    Normal signals wait longer before rebuying the same symbol.
    Exceptional signals get a shorter cooldown but are not unlimited.
    """
    if not symbol:
        return "missing_symbol"

    signal_strength = _feature_value(data, "signal_strength") if isinstance(data, dict) else 0.0
    is_exceptional = _sideways_exceptional_level(signal_strength) > 0 if "_sideways_exceptional_level" in globals() else False

    cooldown = SAME_SYMBOL_EXCEPTIONAL_COOLDOWN_MINUTES if is_exceptional else SAME_SYMBOL_ENTRY_COOLDOWN_MINUTES
    age = _minutes_since_symbol_buy(symbol)

    if age < cooldown:
        return f"same_symbol_cooldown_{age:.1f}_lt_{cooldown:g}_minutes"

    return None


def enforce_entry_quality_lockdown(result, feature_map, regime):
    """
    Final hard firewall before orders are printed/applied.
    It ensures only top-ranked, high-confluence NORMAL features can trade.
    """
    if not ENTRY_QUALITY_LOCKDOWN_ENABLED:
        return result.get("orders", []), []

    orders = result.get("orders", []) if isinstance(result, dict) else []
    if not orders:
        return [], []

    top_symbols, top_ranked, rejected_preview = entry_quality_ranked_symbols(feature_map, regime)

    print(f"ENTRY QUALITY TOP {ENTRY_QUALITY_TOP_N}:", top_ranked)

    filtered = []
    rejected = []

    for order in orders:
        symbol = order.get("symbol")
        side = str(order.get("side", "")).lower()
        strategy = str(order.get("strategy", ""))

        # Sells/exits should not be blocked by entry filters.
        if side == "sell":
            filtered.append(order)
            continue

        if strategy == "raw_momentum_fallback":
            rejected.append({
                "symbol": symbol,
                "reason": "entry_quality_raw_momentum_fallback_disabled",
            })
            continue

        if symbol not in top_symbols:
            rejected.append({
                "symbol": symbol,
                "reason": f"entry_quality_not_top_{ENTRY_QUALITY_TOP_N}",
            })
            continue

        data = feature_map.get(symbol, {})
        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            rejected.append({
                "symbol": symbol,
                "reason": f"entry_quality_{reason}",
            })
            continue

        filtered.append(order)

    if rejected:
        print("ENTRY QUALITY REJECTED:", rejected[:ENTRY_QUALITY_LOG_REJECTION_LIMIT])

    return filtered, rejected

def main():
    load_state_from_db()
    while True:
        try:
            tick = market.tick()
            print("MARKET TICK DATA:", tick["prices"])
            prices = tick["prices"]
            remember_prices(prices)
            refresh_recent_trade_counts()

            features.update(prices)

            f_by_symbol = {s: features.features(s) for s in settings.symbol_list}
            ready = [f for f in f_by_symbol.values() if isinstance(f, dict) and f.get("ready")]
            fallback_features = {}

            if not ready:
                print("WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...")
                fallback_features = build_raw_momentum_fallback(prices)

                if fallback_features:
                    print("FALLBACK FEATURES DIAGNOSTIC ONLY:", fallback_features)
                else:
                    print("FALLBACK FEATURES: {}")

            avg_vol = sum(f["volatility"] for f in ready) / len(ready) if ready else 0
            avg_trend = sum(abs(f["trend"]) for f in ready) / len(ready) if ready else 0

            regime = detect_regime(avg_vol, avg_trend)
            risk.tune(regime)

            equity = portfolio.mark_to_market(prices)

            state = {
                "prices": prices,
                "features": f_by_symbol,
                "equity": equity,
                "cash": portfolio.cash,
                "regime": regime,
            }

            result = agent.run_cycle(state)

            if not isinstance(result, dict):
                result = {"orders": [], "status": "agent_returned_non_dict"}

            proposed_agent_orders = result.get("orders", [])

            if (not proposed_agent_orders) and fallback_features:
                print("FALLBACK TRADING DISABLED: diagnostic fallback ignored; waiting for normal MEXC ranked signals")

            entry_quality_rejections = []
            try:
                result["orders"], entry_quality_rejections = enforce_entry_quality_lockdown(
                    result=result,
                    feature_map=state["features"],
                    regime=regime,
                )
            except Exception as entry_quality_error:
                print("ENTRY QUALITY LOCKDOWN ERROR:", entry_quality_error)

            print("FEATURES:", {k: v for k, v in state["features"].items() if isinstance(v, dict) and v.get("ready")})
            print("ORDERS:", result.get("orders", []))
            proposed_fills = result.get("orders", [])

            applied_fills = []
            rejected = []
            try:
                if entry_quality_rejections:
                    rejected.extend(entry_quality_rejections)
            except NameError:
                pass
            entries_this_cycle = 0

            paused = is_paused()

            if globals().get("last_seen_paused_state") is True and paused is False:
                reset_liquidity_errors()
            
            globals()["last_seen_paused_state"] = paused

            if not paused and pause_reason():
                reset_liquidity_errors()

            if paused:
                rejected.append({
                    "symbol": "ALL",
                    "reason": pause_reason() or "paused",
                })
            else:
                for fill in proposed_fills:
                    strategy = fill.get("strategy")
                    is_shadow = fill.get("shadow_mode", False)
                    symbol = fill["symbol"]
                    side = fill["side"]
                    confidence = float(fill.get("confidence", 0))

                    if side == "buy":
                        allowed, reason = entry_policy_allows(
                            symbol,
                            regime,
                            confidence,
                            entries_this_cycle,
                            strategy=strategy
                        )

                        if not allowed:
                            rejected.append({
                                "symbol": symbol,
                                "reason": reason,
                            })
                            continue

                        # Shadow mode does not affect portfolio or cash
                        if is_shadow:
                            if apply_shadow_buy(fill):
                                applied_fills.append(fill)
                            continue

                        approved, reason = can_buy(symbol, fill, prices, equity)

                        if approved and apply_buy(fill):
                            applied_fills.append(fill)
                            entries_this_cycle += 1
                        else:
                            rejected.append({
                                "symbol": symbol,
                                "reason": reason,
                            })

                    elif side == "sell":
                        applied_fills.append(fill)

            applied_fills.extend(generate_sells(prices, regime))
            applied_fills.extend(emergency_reduce_exposure(prices))

            equity = portfolio.mark_to_market(prices)
            exposure = total_exposure(prices)

            globals()["last_known_equity"] = equity
            globals()["last_known_exposure"] = exposure
            globals()["last_known_regime"] = regime

            if check_daily_loss_guard(equity, exposure, regime):
                proposed_fills = []
                applied_fills = []
                rejected.append({
                    "symbol": "ALL",
                    "reason": "max_daily_loss_auto_pause",
                })

            with engine.begin() as conn:
                filtered_fills = []

                for fill in applied_fills:
                    allowed, reason = final_trade_firewall(fill, regime)

                    if allowed:
                        filtered_fills.append(fill)
                    else:
                        rejected.append({
                            "symbol": fill.get("symbol", "UNKNOWN"),
                            "reason": reason,
                        })

                applied_fills = filtered_fills

                for fill in applied_fills:
                    fill_pnl, original_strat = update_position_from_fill(conn, fill)
                    fill["pnl"] = fill_pnl

                    trades_total.inc()

                    conn.execute(text("""
                        INSERT INTO trades(
                            symbol, side, quantity, expected_price, fill_price,
            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
                        )
                        VALUES(
                            :symbol, :side, :quantity, :expected_price, :fill_price,
                            :slippage_bps, :pnl, :strategy, :confidence, :live, :shadow_mode, DATETIME('now', '+3 hours')
                        )
                    """), fill | {"live": settings.live_trading, "shadow_mode": fill.get("shadow_mode", False)})

                    side = fill.get("side", "").upper()
                    symbol = fill.get("symbol", "")
                    qty = float(fill.get("quantity", 0))
                    price = float(fill.get("fill_price", 0))
                    strategy = fill.get("strategy", "unknown")
                    confidence = float(fill.get("confidence", 0))
                    is_shadow = fill.get("shadow_mode", False)

                    # Sync in-memory Portfolio state from DB to ensure absolute consistency
                    if not is_shadow:
                        pos_row = conn.execute(text("SELECT quantity FROM positions WHERE symbol = :s"), {"s": symbol}).mappings().first()
                        if pos_row:
                            portfolio.positions[symbol] = float(pos_row["quantity"])
                        else:
                            portfolio.positions[symbol] = 0.0

                    send_telegram_alert(
                        f"<b>{side} {'(SHADOW)' if is_shadow else ''}</b> {symbol}\n"
                        f"Qty: {qty:.6f}\n"
                        f"Price: {price:.4f}\n"
                        f"PnL: {fill_pnl:.2f}\n"
                        f"Strategy: {strategy}\n"
                        f"Confidence: {confidence:.2f}\n"
                        f"Live: {settings.live_trading}"
                    )

                    # Strategy scoring: accumulate PnL per strategy, block persistent losers
                    score_strategy = original_strat if side == "SELL" else strategy
                    if score_strategy and score_strategy not in ("take_profit", "single_full_take_profit", "breakeven_protection_exit", "time_stop_exit", "stop_loss", "adaptive_take_profit", "adaptive_stop_loss", "risk_off_exit", "emergency_exposure_reduction", "unknown"):
                        conn.execute(text("""
                            INSERT INTO strategy_scores (strategy, sharpe, drawdown, score, status)
                            VALUES (:strategy, 0, 0, :pnl, 'active')
                            ON CONFLICT DO NOTHING
                        """), {"strategy": score_strategy, "pnl": fill_pnl})
                        
                        conn.execute(text("""
                            UPDATE strategy_scores
                            SET score = score + :pnl, status = CASE WHEN score + :pnl < 0 THEN 'blocked' ELSE 'active' END
                            WHERE strategy = :strategy
                        """), {"strategy": score_strategy, "pnl": fill_pnl})

                mark_positions_to_market(conn, prices)

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
                "shadow_positions": {k: round(v, 6) for k, v in shadow_positions.items() if v > 0},
            })

            try:
                diagnostic_snapshot = {
                    "regime": regime,
                    "equity": round(equity, 2),
                    "cash": round(portfolio.cash, 2),
                    "exposure": round(exposure, 2),
                    "exposure_pct": round(exposure / equity, 4) if equity else 0,
                    "positions": {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0},
                    "risk_status": current_risk_status,
                }

                log_cycle_diagnostic(
                    market_data=prices,
                    features={k: v for k, v in state["features"].items() if isinstance(v, dict) and v.get("ready")},
                    orders=applied_fills,
                    portfolio=diagnostic_snapshot,
                    rejected=rejected,
                    note="main_loop_after_execution",
                )
            except Exception as diagnostic_error:
                print("DIAGNOSTIC_LOG_ERROR:", diagnostic_error)

            time.sleep(settings.trade_interval_seconds)

        except Exception as e:
            error_message = str(e)
            print("Bot loop error:", error_message)

            if "insufficient synthetic liquidity" in error_message.lower():
                if "for " in error_message:
                    symbol = error_message.split("for")[-1].strip()
                    quarantine_symbol(symbol, "liquidity_error_circuit_breaker")
                else:
                    register_liquidity_error(error_message)

            time.sleep(settings.trade_interval_seconds)

if __name__ == "__main__":
    main()






















