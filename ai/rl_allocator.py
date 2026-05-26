import sqlite3
from core.config import settings

STABLE_OR_FIAT_BASES = {
    "USDC", "USD1", "USDT", "BUSD", "TUSD", "DAI", "FDUSD",
    "EUR", "GBP", "TRY", "BRL", "USD", "JPY"
}


def passes_strict_long_filter(f: dict) -> bool:
    try:
        return (
            bool(f.get("ready", False))
            and float(f.get("trend", 0.0)) > 0
            and float(f.get("momentum", 0.0)) > 0
            and float(f.get("one_tick_momentum", 0.0)) > 0
        )
    except Exception:
        return False


def strategy_name(strategy):
    if isinstance(strategy, dict):
        return strategy.get("name", "unknown_strategy")
    return getattr(strategy, "name", "unknown_strategy")


def strategy_value(strategy, field, default):
    if isinstance(strategy, dict):
        return strategy.get(field, default)
    return getattr(strategy, field, default)


def db_strategy_allowed(name: str) -> bool:
    """
    Return True only for strategies that are not blocked and not negative.
    Missing strategy rows are created as active with zero score so new strategies
    can be tested, then blocked later if their score goes negative.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()

        row = cur.execute("""
            SELECT score, status
            FROM strategy_scores
            WHERE strategy = ?
        """, (name,)).fetchone()

        if row is None:
            cur.execute("""
                INSERT OR IGNORE INTO strategy_scores(strategy, score, status)
                VALUES (?, 0.0, 'active')
            """, (name,))
            conn.commit()
            conn.close()
            return True

        score = float(row[0] or 0.0)
        status = str(row[1] or "").lower()

        conn.close()

        if status == "blocked":
            return False
        if score < 0:
            return False
        return True

    except Exception:
        # Fail closed: if we cannot verify the strategy, do not trade it.
        return False


def choose_allowed_strategy(scored):
    for item in scored:
        strategy = item.get("strategy") if isinstance(item, dict) else None
        if strategy is None:
            continue

        name = strategy_name(strategy)
        score = float(item.get("score", 0.0) or 0.0)

        if score <= 0:
            continue

        if db_strategy_allowed(name):
            return item

    return None



def is_symbol_quarantined(symbol: str) -> bool:
    """
    Skip symbols that are still actively quarantined.
    Uses Kenya-time DB convention.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()
        row = cur.execute("""
            SELECT symbol
            FROM symbol_quarantine
            WHERE symbol = ?
              AND blocked_until IS NOT NULL
              AND blocked_until > datetime('now', '+3 hours')
        """, (symbol,)).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def is_already_holding(symbol: str, market_state: dict) -> bool:
    """
    Skip symbols already held either in market_state or in the DB positions table.
    This prevents allocator orders that execution later rejects as already_holding_symbol.
    """
    try:
        positions = market_state.get("positions", {}) or {}
        qty = positions.get(symbol, 0)
        if float(qty or 0) > 0.00000001:
            return True
    except Exception:
        pass

    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()
        row = cur.execute("""
            SELECT quantity
            FROM positions
            WHERE symbol = ?
        """, (symbol,)).fetchone()
        conn.close()

        if row and float(row[0] or 0) > 0.00000001:
            return True
    except Exception:
        pass

    return False


def has_bad_symbol_history(symbol: str) -> bool:
    """
    Skip symbols where historical stop-loss exits exceed take-profit exits.
    This mirrors the execution-layer symbol_bad_history rule.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()
        row = cur.execute("""
            SELECT
                SUM(CASE WHEN strategy='stop_loss' THEN 1 ELSE 0 END) AS stop_losses,
                SUM(CASE WHEN strategy='take_profit' THEN 1 ELSE 0 END) AS take_profits
            FROM trades
            WHERE symbol = ?
              AND side = 'sell'
              AND created_at >= datetime('now', '+3 hours', '-3 hours')
        """, (symbol,)).fetchone()
        conn.close()

        if not row:
            return False

        stop_losses = int(row[0] or 0)
        take_profits = int(row[1] or 0)

        # Softer recent-history rule:
        # block only symbols with repeated recent stop-loss dominance.
        return stop_losses >= 3 and stop_losses >= (take_profits + 2)
    except Exception:
        return False


def is_symbol_in_cooldown(symbol: str) -> bool:
    """
    Skip symbols recently traded so allocator does not create orders
    that execution later rejects as cooldown.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()

        row = cur.execute("""
            SELECT created_at
            FROM trades
            WHERE symbol = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (symbol,)).fetchone()

        conn.close()

        if not row or not row[0]:
            return False

        from datetime import datetime, timedelta

        last_trade = datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
        now = datetime.now()

        cooldown_seconds = int(getattr(settings, "cooldown_seconds", 600) or 600)

        return now < last_trade + timedelta(seconds=cooldown_seconds)

    except Exception:
        return False


def has_hit_max_trades_per_symbol(symbol: str) -> bool:
    """
    Skip symbols that already reached the configured max_trades_per_symbol.
    Mirrors execution-layer max_trades_per_symbol rule.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()

        row = cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE symbol = ?
        """, (symbol,)).fetchone()

        conn.close()

        trade_count = int(row[0] or 0) if row else 0
        max_trades = int(getattr(settings, "max_trades_per_symbol", 7) or 7)

        return trade_count >= max_trades

    except Exception:
        return False


def has_hit_hourly_entry_cap(regime: str) -> bool:
    """
    Return True when recent buy entries already reached the configured
    per-hour cap for the current market regime.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()

        row = cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE side = 'buy'
              AND created_at >= datetime('now', '+3 hours', '-1 hour')
        """).fetchone()

        conn.close()

        buys_last_hour = int(row[0] or 0) if row else 0
        regime = str(regime or "").upper()

        if regime == "SIDEWAYS":
            limit = int(getattr(settings, "sideways_max_entries_per_hour", 8) or 8)
        else:
            limit = int(getattr(settings, "trending_max_entries_per_hour", 16) or 16)

        return buys_last_hour >= limit

    except Exception as e:
        print(f"ALLOCATOR WARN: hourly cap check failed: {e}")
        return False


def recent_stop_loss_ratio(limit=10):
    """
    Looks at recent closed sells.
    If stop_loss dominates, allocator becomes more selective and smaller.
    """
    try:
        conn = sqlite3.connect("quant.db")
        cur = conn.cursor()

        rows = cur.execute("""
            SELECT strategy
            FROM trades
            WHERE side = 'sell'
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        conn.close()

        if not rows:
            return 0.0

        stop_losses = sum(
            1 for r in rows
            if str(r[0] or "").lower() == "stop_loss"
        )

        return stop_losses / len(rows)

    except Exception as e:
        print(f"ALLOCATOR WARN: stop loss ratio check failed: {e}")
        return 0.0

class SimpleAllocator:
    def allocate(self, scored, market_state):
        if not scored:
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        best = choose_allowed_strategy(scored)
        if not best:
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        features = market_state.get("features", {})
        equity = float(market_state.get("equity", 0) or 0)
        remaining_cash = float(market_state.get("cash", equity) or 0)
        risk_status = str(market_state.get("risk_status", "") or "").upper()

        if risk_status == "BLOCKED":
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        if not features or equity <= 0 or remaining_cash <= 0:
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        strategy = best.get("strategy")
        if strategy is None:
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        max_orders = int(settings.max_new_entries_per_cycle)
        size_multiplier = 1.0

        # Reduce trading while account is in CAUTION.
        if risk_status == "CAUTION":
            max_orders = 1
            size_multiplier = 0.50

        # Also reduce sizing below 98% of initial capital.
        starting_equity = float(getattr(settings, "starting_equity", 10.0) or 10.0)
        if equity < starting_equity * 0.98:
            size_multiplier = min(size_multiplier, 0.50)

        orders = []

        # Adaptive scout mode:
        # If recent closed trades are mostly stop-losses, do not stop completely.
        # Instead, reduce position size and demand stronger signals.
        sl_ratio = recent_stop_loss_ratio(10)

        if sl_ratio >= 0.60:
            print(f"ALLOCATOR MODE: SCOUT stop_loss_ratio={sl_ratio:.2f}")
            size_multiplier = min(size_multiplier, 0.25)
            min_signal_strength = 0.030
        elif sl_ratio >= 0.40:
            print(f"ALLOCATOR MODE: DEFENSIVE stop_loss_ratio={sl_ratio:.2f}")
            size_multiplier = min(size_multiplier, 0.50)
            min_signal_strength = 0.025
        else:
            min_signal_strength = 0.018

        regime = str(market_state.get("regime", "") or "").upper()
        if has_hit_hourly_entry_cap(regime):
            print(f"ALLOCATOR BLOCK: {regime}_max_entries_per_hour_hit")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        ranked = sorted(
            features.items(),
            key=lambda item: item[1].get("signal_strength", 0.0),
            reverse=True,
        )

        for symbol, f in ranked:
            if len([o for o in orders if not o.get("shadow_mode")]) >= max_orders:
                break

            if is_already_holding(symbol, market_state):
                print(f"ALLOCATOR SKIP {symbol}: already_holding")
                continue

            if is_symbol_quarantined(symbol):
                print(f"ALLOCATOR SKIP {symbol}: symbol_quarantined")
                continue

            if has_bad_symbol_history(symbol):
                print(f"ALLOCATOR SKIP {symbol}: symbol_bad_history")
                continue

            if is_symbol_in_cooldown(symbol):
                print(f"ALLOCATOR SKIP {symbol}: cooldown")
                continue

            if has_hit_max_trades_per_symbol(symbol):
                print(f"ALLOCATOR SKIP {symbol}: max_trades_per_symbol")
                continue

            if not passes_strict_long_filter(f):
                print(
                    f"ALLOCATOR SKIP {symbol}: strict_filter "
                    f"trend={float(f.get('trend', 0.0)):.5f} "
                    f"momentum={float(f.get('momentum', 0.0)):.5f} "
                    f"one_tick={float(f.get('one_tick_momentum', 0.0)):.5f}"
                )
                continue

            base = symbol.split("/")[0].upper()
            if settings.stablecoin_filter_enabled and base in STABLE_OR_FIAT_BASES:
                continue

            trend_ok = float(f.get("trend", 0.0)) > float(strategy_value(strategy, "trend_threshold", 0.0))
            momentum_ok = float(f.get("momentum", 0.0)) > float(strategy_value(strategy, "momentum_threshold", 0.0))
            long_trend_ok = float(f.get("long_trend", 0.0)) >= -0.003
            one_tick_ok = float(f.get("one_tick_momentum", 0.0)) > 0

            if not (trend_ok and momentum_ok and long_trend_ok and one_tick_ok):
                continue

            notional = equity * float(strategy_value(strategy, "risk_fraction", 0.02)) * size_multiplier
            notional = min(notional, remaining_cash)

            if notional < settings.min_trade_notional:
                continue

            price = float(f["price"])
            qty = notional / price

            orders.append({
                "symbol": symbol,
                "side": "buy",
                "qty": qty,
                "price": price,
                "strategy": strategy_name(strategy),
                "confidence": float(best.get("score", 0.0) or 0.0),
                "shadow_mode": False,
            })

            remaining_cash -= notional
            if remaining_cash < settings.min_trade_notional:
                break

        leverage = (equity - remaining_cash) / equity if equity else 0
        return {
            "orders": orders[:1],
            "leverage": leverage,
            "estimated_var": 0.005 if orders else 0,
        }


RLAllocator = SimpleAllocator





















