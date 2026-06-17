from core.db import engine
import os
from sqlalchemy import text
from core.config import settings


STABLE_OR_FIAT_BASES = {
    "USDC", "USD1", "USDT", "BUSD", "TUSD", "DAI", "FDUSD",
    "EUR", "GBP", "TRY", "BRL", "USD", "JPY"
}



BLOCKED_EXECUTABLE_SOURCES = {
    "FALLBACK_SCOUT_BREAKOUT",
    "RAW_MOMENTUM_FALLBACK",
    "RAW_MOMENTUM_FALLBACK_DIAGNOSTIC",
    "RAW_MOMENTUM_FALLBACK_DISABLED",
    "RAW_MOMENTUM_FALLBACK_ENTRY",
    "RAW_MOMENTUM_FALLBACK_ORDER",
    "RAW_MOMENTUM_FALLBACK_SELECTED",
    "RAW_MOMENTUM_FALLBACK_EXECUTED",
    "RAW_MOMENTUM_FALLBACK_EXECUTABLE",
    "RAW_MOMENTUM",
}


def safe_float(value, default=0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def feature_source(f: dict) -> str:
    return str((f or {}).get("source", "")).strip().upper()


def feature_source_is_normal(f: dict) -> bool:
    return feature_source(f) == "NORMAL"


def feature_rejection_reason(symbol: str, f: dict) -> str | None:
    if not isinstance(f, dict):
        return "feature_not_dict"

    if not bool(f.get("ready", False)):
        return "feature_not_ready"

    source = feature_source(f)
    if source != "NORMAL":
        if source in BLOCKED_EXECUTABLE_SOURCES or "FALLBACK" in source or "SCOUT" in source or "RAW_MOMENTUM" in source:
            return "raw_momentum_fallback_disabled"
        return f"feature_source_not_normal:{source or 'missing'}"

    if safe_float(f.get("price", 0.0), 0.0) <= 0:
        return "invalid_price"

    try:
        float(f.get("signal_strength", 0.0) or 0.0)
    except Exception:
        return "invalid_signal_strength"

    if not str(f.get("symbol_regime", "") or "").strip():
        return "missing_symbol_regime"

    return None


def allocator_feature_snapshot(f: dict, fallback_confidence: float) -> dict:
    return {
        "ready": bool(f.get("ready", False)),
        "source": feature_source(f),
        "price": safe_float(f.get("price", 0.0)),
        "trend": safe_float(f.get("trend", 0.0)),
        "long_trend": safe_float(f.get("long_trend", 0.0)),
        "momentum": safe_float(f.get("momentum", 0.0)),
        "one_tick_momentum": safe_float(f.get("one_tick_momentum", 0.0)),
        "signal_strength": safe_float(f.get("signal_strength", 0.0)),
        "confidence": safe_float(f.get("confidence", fallback_confidence), fallback_confidence),
        "symbol_regime": str(f.get("symbol_regime", "") or ""),
        "trend_quality": safe_float(f.get("trend_quality", 0.0)),
        "breakout_score": safe_float(f.get("breakout_score", 0.0)),
    }



def is_strong_symbol_uptrend(f: dict) -> bool:
    """
    Normal-feature only.
    Allows a strong individual coin trend even if global market regime is RISK_OFF.
    """
    try:
        if not bool(f.get("ready", False)):
            return False

        if not feature_source_is_normal(f):
            return False

        symbol_regime = str(f.get("symbol_regime", "")).upper()
        trend = float(f.get("trend", 0.0) or 0.0)
        long_trend = float(f.get("long_trend", 0.0) or 0.0)
        momentum = float(f.get("momentum", 0.0) or 0.0)
        one_tick = float(f.get("one_tick_momentum", 0.0) or 0.0)
        signal = float(f.get("signal_strength", 0.0) or 0.0)
        quality = float(f.get("trend_quality", 0.0) or 0.0)

        if symbol_regime not in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"):
            return False

        if trend <= 0 or momentum <= 0 or signal <= 0:
            return False

        if one_tick < -0.0015:
            return False

        # Strong enough for defensive RISK_OFF participation.
        return signal >= 0.004 or quality >= 0.004 or (trend > 0.002 and momentum > 0.002)

    except Exception:
        return False


def risk_off_size_multiplier(regime: str, f: dict) -> float:
    """
    Global regime is market weather.
    Symbol regime is the coin's own behavior.

    During RISK_OFF, only allow strong symbol uptrends, and reduce size.
    """
    if str(regime).upper() != "RISK_OFF":
        return 1.0

    if is_strong_symbol_uptrend(f):
        return 0.35

    return 0.0


def passes_strict_long_filter(f: dict) -> bool:
    """
    Normal-feature long filter.

    Global regime is market weather.
    Symbol regime is each coin's own behavior.

    This blocks raw momentum fallback and allows flat one-tick movement
    when trend and multi-tick momentum are positive.
    """
    try:
        if not bool(f.get("ready", False)):
            return False

        if not feature_source_is_normal(f):
            return False

        trend = float(f.get("trend", 0.0) or 0.0)
        momentum = float(f.get("momentum", 0.0) or 0.0)
        one_tick = float(f.get("one_tick_momentum", 0.0) or 0.0)
        signal = float(f.get("signal_strength", 0.0) or 0.0)
        symbol_regime = str(f.get("symbol_regime", "")).upper()

        if symbol_regime in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"):
            return trend > 0 and momentum > 0 and signal > 0 and one_tick >= -0.0015

        return trend > 0 and momentum > 0 and signal > 0 and one_tick >= -0.0015

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
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT score, status
                FROM strategy_scores
                WHERE strategy = :name
            """), {"name": name}).mappings().first()

            if row is None:
                conn.execute(text("""
                    INSERT INTO strategy_scores (strategy, score, status)
                    VALUES (:name, 0.0, 'active')
                    ON CONFLICT (strategy) DO NOTHING
                """), {"name": name})
                return True

            score = float(row["score"] or 0.0)
            status = str(row["status"] or "").lower()

        if status == "blocked":
            return False
        if score < 0:
            return False
        return True

    except Exception as e:
        live = str(getattr(settings, "live_trading", False)).lower() in ("1", "true", "yes", "on")
        if not live:
            print(f"ALLOCATOR WARN: strategy DB check failed for {name}; allowing in paper mode: {e}")
            return True
        # Fail closed only in live mode.
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
    Timestamps stored as Kenya time (UTC+3); CURRENT_TIMESTAMP + interval '3 hours' = Kenya now.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT symbol
                FROM symbol_quarantine
                WHERE symbol = :symbol
                  AND blocked_until IS NOT NULL
                  AND blocked_until > CURRENT_TIMESTAMP + interval '3 hours'
            """), {"symbol": symbol}).mappings().first()
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
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT quantity
                FROM positions
                WHERE symbol = :symbol
            """), {"symbol": symbol}).mappings().first()

        if row and float(row["quantity"] or 0) > 0.00000001:
            return True
    except Exception:
        pass

    return False


def has_bad_symbol_history(symbol: str) -> bool:
    """
    Skip symbols where historical stop-loss exits exceed take-profit exits.
    This mirrors the execution-layer symbol_bad_history rule.
    Checks the last 3 hours (Kenya time: CURRENT_TIMESTAMP + interval '3 hours' - interval '3 hours' = CURRENT_TIMESTAMP).
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('stop_loss','stop_loss_exit','adaptive_stop_loss') THEN 1 ELSE 0 END) AS stop_losses,
                    SUM(CASE WHEN LOWER(TRIM(strategy)) IN ('take_profit','adaptive_take_profit') THEN 1 ELSE 0 END) AS take_profits
                FROM trades
                WHERE symbol = :symbol
                  AND side = 'sell'
                  AND created_at >= CURRENT_TIMESTAMP
            """), {"symbol": symbol}).mappings().first()

        if not row:
            return False

        stop_losses = int(row["stop_losses"] or 0)
        take_profits = int(row["take_profits"] or 0)

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
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT created_at
                FROM trades
                WHERE symbol = :symbol
                ORDER BY created_at DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

        if not row or not row["created_at"]:
            return False

        from datetime import datetime, timedelta, timezone

        last_trade_raw = row["created_at"]
        # Handle both string and datetime objects from the DB
        if isinstance(last_trade_raw, str):
            last_trade = datetime.strptime(str(last_trade_raw)[:19], "%Y-%m-%d %H:%M:%S")
        else:
            last_trade = last_trade_raw
            if hasattr(last_trade, 'tzinfo') and last_trade.tzinfo is not None:
                last_trade = last_trade.replace(tzinfo=None)

        now = datetime.utcnow()
        cooldown_seconds = int(getattr(settings, "cooldown_seconds", 600) or 600)

        return now < last_trade + timedelta(seconds=cooldown_seconds)

    except Exception:
        return False


def has_hit_max_trades_per_symbol(symbol: str) -> bool:
    """
    Rolling-window max trades check.

    Old behavior counted all historical trades and blocked symbols for too long.
    New behavior only counts recent BUY trades inside TRADE_COUNT_WINDOW_HOURS.
    """
    try:
        max_trades = int(getattr(settings, "max_trades_per_symbol", 7) or 7)
        window_hours = float(getattr(settings, "trade_count_window_hours", 4) or 4)

        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS count
                FROM trades
                WHERE symbol = :symbol
                  AND side = 'buy'
                  AND created_at >= (CURRENT_TIMESTAMP + interval '3 hours' - (:hours || ' hours')::interval)
            """), {
                "symbol": symbol,
                "hours": window_hours,
            }).mappings().first()

        recent_count = int(row["count"] or 0) if row else 0

        if recent_count >= max_trades:
            print(
                f"ALLOCATOR TRADE WINDOW {symbol}: "
                f"{recent_count}/{max_trades} buys in {window_hours:g}h"
            )
            return True

        return False

    except Exception as e:
        print(f"ALLOCATOR TRADE COUNT CHECK ERROR {symbol}: {e}")
        return False


def has_hit_hourly_entry_cap(regime: str) -> bool:
    """
    Return True when recent buy entries already reached the configured
    per-hour cap for the current market regime.
    Timestamps stored as Kenya time (UTC+3); last hour = CURRENT_TIMESTAMP + interval '2 hours'.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS count
                FROM trades
                WHERE side = 'buy'
                  AND created_at >= CURRENT_TIMESTAMP + interval '2 hours'
            """)).mappings().first()

        buys_last_hour = int(row["count"] or 0) if row else 0
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
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT strategy
                FROM trades
                WHERE side = 'sell'
                ORDER BY created_at DESC
                LIMIT :limit
            """), {"limit": limit}).mappings().all()

        if not rows:
            return 0.0

        stop_losses = sum(
            1 for r in rows
            if str(r["strategy"] or "").lower() in ("stop_loss", "stop_loss_exit", "adaptive_stop_loss")
        )

        return stop_losses / len(rows)

    except Exception as e:
        print(f"ALLOCATOR WARN: stop loss ratio check failed: {e}")
        return 0.0

class SimpleAllocator:
    def allocate(self, scored, market_state):
        if not scored:
            print("ALLOCATOR BLOCK: no_scored_strategies")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        best = choose_allowed_strategy(scored)
        if not best:
            top_scores = [
                {
                    "strategy": strategy_name(x.get("strategy")) if isinstance(x, dict) else "unknown",
                    "score": round(float(x.get("score", 0) or 0), 4),
                    "matches": x.get("matches", 0),
                }
                for x in scored[:5]
                if isinstance(x, dict)
            ]
            print(f"ALLOCATOR BLOCK: no_allowed_positive_strategy top_scores={top_scores}")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        features = market_state.get("features", {})
        equity = float(market_state.get("equity", 0) or 0)
        remaining_cash = float(market_state.get("cash", equity) or 0)
        risk_status = str(market_state.get("risk_status", "") or "").upper()

        if risk_status == "BLOCKED":
            print("ALLOCATOR BLOCK: risk_status_BLOCKED")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        if not features or equity <= 0 or remaining_cash <= 0:
            print(f"ALLOCATOR BLOCK: missing_inputs features={len(features) if isinstance(features, dict) else 0} equity={equity} cash={remaining_cash}")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        strategy = best.get("strategy")
        if strategy is None:
            print("ALLOCATOR BLOCK: best_strategy_missing")
            return {"orders": [], "leverage": 0, "estimated_var": 0}
        print(f"ALLOCATOR BEST: strategy={strategy_name(strategy)} score={float(best.get('score', 0) or 0):.4f} matches={best.get('matches', 0)}")

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

        regime = str(market_state.get("regime", "") or "").upper()
        if regime == "SIDEWAYS":
            min_signal_strength = safe_float(getattr(settings, "entry_min_signal_sideways", 0.0015), 0.0015)
        else:
            min_signal_strength = safe_float(getattr(settings, "entry_min_signal_trending", 0.0010), 0.0010)

        if sl_ratio >= 0.60:
            print(f"ALLOCATOR MODE: SCOUT stop_loss_ratio={sl_ratio:.2f}")
            size_multiplier = min(size_multiplier, 0.25)
            min_signal_strength = max(min_signal_strength, 0.0030)
        elif sl_ratio >= 0.40:
            print(f"ALLOCATOR MODE: DEFENSIVE stop_loss_ratio={sl_ratio:.2f}")
            size_multiplier = min(size_multiplier, 0.50)
            min_signal_strength = max(min_signal_strength, 0.0025)

        min_confidence = (
            safe_float(getattr(settings, "sideways_min_confidence", 0.50), 0.50)
            if regime == "SIDEWAYS"
            else safe_float(getattr(settings, "min_entry_confidence", 0.50), 0.50)
        )
        strategy_confidence = safe_float(best.get("score", 0.0), 0.0)

        if strategy_confidence < min_confidence:
            print(
                f"ALLOCATOR BLOCK: weak_confidence "
                f"confidence={strategy_confidence:.4f} min_confidence={min_confidence:.4f} regime={regime}"
            )
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        if has_hit_hourly_entry_cap(regime):
            print(f"ALLOCATOR BLOCK: {regime}_max_entries_per_hour_hit")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        ranked_all = []

        for symbol, f in features.items():
            reject_reason = feature_rejection_reason(symbol, f)
            if reject_reason:
                print(f"ALLOCATOR SKIP {symbol}: {reject_reason}")
                continue

            ranked_all.append((
                symbol,
                f,
                safe_float(f.get("signal_strength", 0.0)),
                safe_float(f.get("trend", 0.0)),
                safe_float(f.get("momentum", 0.0)),
                safe_float(f.get("confidence", best.get("score", 0.0)), best.get("score", 0.0)),
            ))

        ranked_all = sorted(
            ranked_all,
            key=lambda item: (item[2], item[3], item[4], item[5]),
            reverse=True,
        )

        quality_top_n = int(getattr(settings, "entry_quality_top_n", 10) or 10)
        top_quality_rows = ranked_all[:quality_top_n]
        top_quality_symbols = {symbol for symbol, *_rest in top_quality_rows}

        print(
            "ENTRY QUALITY TOP 10:",
            [
                (
                    symbol,
                    round(signal, 6),
                    round(trend, 6),
                    round(momentum, 6),
                    round(confidence, 4),
                    str(f.get("symbol_regime", "")),
                )
                for symbol, f, signal, trend, momentum, confidence in top_quality_rows
            ],
        )

        if not top_quality_rows:
            print("ALLOCATOR BLOCK: no_candidate_passed reason=no_trusted_normal_features")
            return {"orders": [], "leverage": 0, "estimated_var": 0}

        ranked = [(symbol, f) for symbol, f, *_rest in ranked_all]

        for symbol, f in ranked:
            if len([o for o in orders if not o.get("shadow_mode")]) >= max_orders:
                break

            if symbol not in top_quality_symbols:
                print(f"ALLOCATOR SKIP {symbol}: not_top_quality")
                continue

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
                print(f"ALLOCATOR SKIP {symbol}: max_trades_per_symbol_recent_window")
                continue

            if not passes_strict_long_filter(f):
                print(
                    f"ALLOCATOR SKIP {symbol}: strict_filter "
                    f"trend={float(f.get('trend', 0.0)):.5f} "
                    f"momentum={float(f.get('momentum', 0.0)):.5f} "
                    f"one_tick={float(f.get('one_tick_momentum', 0.0)):.5f}"
                )
                continue

            signal_strength = safe_float(f.get("signal_strength", 0.0), 0.0)
            if signal_strength < min_signal_strength:
                print(
                    f"ALLOCATOR SKIP {symbol}: weak_signal "
                    f"signal={signal_strength:.5f} min_signal={min_signal_strength:.5f} regime={regime}"
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
                print(
                    f"ALLOCATOR SKIP {symbol}: strategy_threshold "
                    f"trend_ok={trend_ok} momentum_ok={momentum_ok} "
                    f"long_trend_ok={long_trend_ok} one_tick_ok={one_tick_ok} "
                    f"trend={float(f.get('trend', 0.0)):.5f} "
                    f"momentum={float(f.get('momentum', 0.0)):.5f} "
                    f"one_tick={float(f.get('one_tick_momentum', 0.0)):.5f} "
                    f"signal={float(f.get('signal_strength', 0.0)):.5f}"
                )
                continue

            notional = equity * float(strategy_value(strategy, "risk_fraction", 0.02)) * size_multiplier
            notional = min(notional, remaining_cash)

            if notional < settings.min_trade_notional:
                continue

            price = float(f["price"])
            qty = notional / price

            if str(regime).upper() == "RISK_OFF" and not is_strong_symbol_uptrend(f):
                print(f"ALLOCATOR SKIP {symbol}: global_RISK_OFF_without_symbol_uptrend symbol_regime={f.get('symbol_regime')}")
                continue

            order_confidence = safe_float(best.get("score", 0.0), 0.0)
            feature_snapshot = allocator_feature_snapshot(f, order_confidence)

            print(
                f"ALLOCATOR_RESCUE selected {symbol} "
                f"source={feature_snapshot['source']} ready={feature_snapshot['ready']} "
                f"strategy={strategy_name(strategy)} confidence={order_confidence:.4f} "
                f"signal_strength={feature_snapshot['signal_strength']:.5f} "
                f"symbol_regime={feature_snapshot['symbol_regime']} "
                f"entry_reason=evo_allocator_rescue_normal_top_quality"
            )

            orders.append({
                "symbol": symbol,
                "side": "buy",
                "qty": qty,
                "price": price,
                "strategy": strategy_name(strategy),
                "confidence": order_confidence,
                "shadow_mode": False,
                "feature_source": feature_snapshot["source"],
                "signal_strength": feature_snapshot["signal_strength"],
                "symbol_regime": feature_snapshot["symbol_regime"],
                "entry_reason": "evo_allocator_rescue_normal_top_quality",
                "feature": feature_snapshot,
            })

            remaining_cash -= notional
            if remaining_cash < settings.min_trade_notional:
                break

        if not orders:
            print("ALLOCATOR BLOCK: no_candidate_passed reason=quality_or_risk_gates")

        leverage = (equity - remaining_cash) / equity if equity else 0
        return {
            "orders": orders[:1],
            "leverage": leverage,
            "estimated_var": 0.005 if orders else 0,
        }


RLAllocator = SimpleAllocator
