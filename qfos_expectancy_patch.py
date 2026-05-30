
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "qfos_expectancy_config.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "sideways_scout_notional_usd": 1.00,
    "trend_scout_notional_usd": 1.50,
    "min_trade_price": 0.01,
    "min_scout_signal_sideways": 0.004,
    "min_scout_trend_quality": 0.001,
    "max_scout_volatility_sideways": 0.006,
    "require_positive_one_tick_for_scout": True,
    "fallback_stop_loss_pct": -0.006,
    "breakeven_arm_pct": 0.0035,
    "breakeven_exit_floor_pct": 0.0004,
    "trailing_arm_pct": 0.005,
    "trailing_giveback_pct": 0.0025,
    "sideways_time_stop_minutes": 45,
    "sideways_time_stop_min_pnl": -0.0015,
    "sideways_time_stop_max_pnl": 0.002,
    "symbol_cooldown_after_losses": 2,
    "cooldown_minutes": 180,
    "state_file": "qfos_expectancy_state.json",
    "decision_log": "qfos_expectancy_decisions.jsonl",
}

def _load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CONFIG)
            merged.update(data or {})
            return merged
    except Exception:
        pass
    return dict(DEFAULT_CONFIG)

CFG = _load_config()
STATE_PATH = ROOT / str(CFG.get("state_file", "qfos_expectancy_state.json"))
DECISION_LOG = ROOT / str(CFG.get("decision_log", "qfos_expectancy_decisions.jsonl"))

def _now_ts() -> float:
    return time.time()

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def _safe_str(x: Any, default: str = "") -> str:
    try:
        return str(x)
    except Exception:
        return default

def _read_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("positions", {})
                data.setdefault("losses", {})
                return data
    except Exception:
        pass
    return {"positions": {}, "losses": {}}

def _write_state(state: Dict[str, Any]) -> None:
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception:
        pass

def _log(action: str, **payload: Any) -> None:
    try:
        row = {"ts": _utc(), "action": action}
        row.update(payload)
        with DECISION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass

def _as_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj

    data = {}
    for name in (
        "symbol", "quantity", "qty", "avg_entry", "entry_price",
        "mark_price", "price", "strategy", "created_at", "updated_at"
    ):
        if hasattr(obj, name):
            try:
                data[name] = getattr(obj, name)
            except Exception:
                pass
    return data

def _get_feature(features: Any, symbol: str) -> Dict[str, Any]:
    if isinstance(features, dict):
        value = features.get(symbol, {})
        if isinstance(value, dict):
            return value
    return {}

def _get_price(symbol: str, pos: Dict[str, Any], feature: Dict[str, Any], prices: Any) -> float:
    if isinstance(prices, dict) and symbol in prices:
        p = _safe_float(prices.get(symbol))
        if p > 0:
            return p

    p = _safe_float(feature.get("price"))
    if p > 0:
        return p

    p = _safe_float(pos.get("mark_price"))
    if p > 0:
        return p

    return _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price"))

def _get_positions(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = (
        ctx.get("positions")
        or ctx.get("open_positions")
        or ctx.get("portfolio_positions")
        or {}
    )

    out: Dict[str, Dict[str, Any]] = {}

    if isinstance(raw, dict):
        for sym, pos in raw.items():
            pd = _as_dict(pos)
            symbol = _safe_str(pd.get("symbol") or sym)
            if symbol:
                out[symbol] = pd

    elif isinstance(raw, list):
        for pos in raw:
            pd = _as_dict(pos)
            symbol = _safe_str(pd.get("symbol"))
            if symbol:
                out[symbol] = pd

    return out

def _order_symbol(order: Dict[str, Any]) -> str:
    return _safe_str(order.get("symbol"))

def _order_side(order: Dict[str, Any]) -> str:
    return _safe_str(order.get("side")).lower()

def _order_strategy(order: Dict[str, Any]) -> str:
    return _safe_str(order.get("strategy") or order.get("reason") or "unknown")

def _order_price(order: Dict[str, Any], feature: Dict[str, Any]) -> float:
    p = _safe_float(order.get("fill_price") or order.get("expected_price") or order.get("price"))
    if p > 0:
        return p
    return _safe_float(feature.get("price"))

def _recent_losses(symbol: str, state: Dict[str, Any], minutes: float) -> int:
    losses = state.get("losses", {}).get(symbol, [])
    now = _now_ts()
    cutoff = now - minutes * 60
    fresh = [x for x in losses if _safe_float(x.get("ts")) >= cutoff]
    state.setdefault("losses", {})[symbol] = fresh
    return len(fresh)

def _record_loss(symbol: str, strategy: str, state: Dict[str, Any]) -> None:
    if not symbol:
        return
    state.setdefault("losses", {}).setdefault(symbol, []).append({
        "ts": _now_ts(),
        "strategy": strategy,
    })

def _record_buy(order: Dict[str, Any], feature: Dict[str, Any], state: Dict[str, Any]) -> None:
    symbol = _order_symbol(order)
    if not symbol:
        return

    price = _order_price(order, feature)
    if price <= 0:
        return

    state.setdefault("positions", {})[symbol] = {
        "entry_ts": _now_ts(),
        "entry_price": price,
        "highest_price": price,
        "highest_pnl_pct": 0.0,
        "strategy": _order_strategy(order),
        "signal_strength": _safe_float(order.get("signal_strength") or feature.get("signal_strength")),
    }

def _ensure_position_meta(symbol: str, pos: Dict[str, Any], price: float, state: Dict[str, Any]) -> Dict[str, Any]:
    positions = state.setdefault("positions", {})
    meta = positions.get(symbol)

    entry = _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price") or price)

    if not isinstance(meta, dict):
        meta = {
            "entry_ts": _now_ts(),
            "entry_price": entry if entry > 0 else price,
            "highest_price": price,
            "highest_pnl_pct": 0.0,
            "strategy": _safe_str(pos.get("strategy") or "recovered_position"),
            "signal_strength": 0.0,
        }
        positions[symbol] = meta

    if price > _safe_float(meta.get("highest_price")):
        meta["highest_price"] = price

    entry_price = _safe_float(meta.get("entry_price") or entry)
    if entry_price > 0 and price > 0:
        pnl_pct = (price - entry_price) / entry_price
        meta["highest_pnl_pct"] = max(_safe_float(meta.get("highest_pnl_pct")), pnl_pct)

    return meta

def _make_sell(symbol: str, qty: float, price: float, reason: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": price,
        "fill_price": price,
        "slippage_bps": 0,
        "strategy": reason,
        "confidence": 1.0,
    }

def _has_pending_sell(symbol: str, orders: List[Dict[str, Any]]) -> bool:
    for o in orders:
        if _order_symbol(o) == symbol and _order_side(o) == "sell":
            return True
    return False

def _filter_and_resize_orders(
    orders: List[Dict[str, Any]],
    ctx: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:

    if not orders:
        return []

    features = ctx.get("features") or ctx.get("feature_map") or {}

    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()
    if not regime:
        portfolio = ctx.get("portfolio")
        if isinstance(portfolio, dict):
            regime = _safe_str(portfolio.get("regime")).upper()

    filtered: List[Dict[str, Any]] = []

    for order in orders:
        if not isinstance(order, dict):
            filtered.append(order)
            continue

        symbol = _order_symbol(order)
        side = _order_side(order)
        strategy = _order_strategy(order)
        feature = _get_feature(features, symbol)
        price = _order_price(order, feature)

        if side == "sell":
            if "stop_loss" in strategy:
                _record_loss(symbol, strategy, state)
            filtered.append(order)
            continue

        if side != "buy":
            filtered.append(order)
            continue

        is_scout = strategy == "fallback_scout_breakout"

        if not is_scout:
            _record_buy(order, feature, state)
            filtered.append(order)
            continue

        symbol_regime = _safe_str(feature.get("symbol_regime")).upper()
        signal = _safe_float(order.get("signal_strength") or feature.get("signal_strength"))
        trend_quality = _safe_float(feature.get("trend_quality") or feature.get("symbol_trend_score"))
        volatility = _safe_float(feature.get("volatility"))
        one_tick = _safe_float(feature.get("one_tick_momentum"))

        if price <= 0 or price < _safe_float(CFG["min_trade_price"]):
            _log("BLOCK_ENTRY", symbol=symbol, reason="price_too_low", price=price, strategy=strategy)
            continue

        cooldown_losses = _recent_losses(symbol, state, _safe_float(CFG["cooldown_minutes"]))

        if cooldown_losses >= int(CFG["symbol_cooldown_after_losses"]):
            _log("BLOCK_ENTRY", symbol=symbol, reason="loss_cooldown", losses=cooldown_losses, strategy=strategy)
            continue

        if "SIDEWAYS" in regime:
            if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):
                _log("BLOCK_ENTRY", symbol=symbol, reason="not_clean_uptrend", symbol_regime=symbol_regime, strategy=strategy)
                continue

            if signal < _safe_float(CFG["min_scout_signal_sideways"]):
                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_signal", signal=signal, strategy=strategy)
                continue

            if trend_quality < _safe_float(CFG["min_scout_trend_quality"]):
                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_trend_quality", trend_quality=trend_quality, strategy=strategy)
                continue

            if volatility > _safe_float(CFG["max_scout_volatility_sideways"]):
                _log("BLOCK_ENTRY", symbol=symbol, reason="extreme_volatility", volatility=volatility, strategy=strategy)
                continue

            if bool(CFG["require_positive_one_tick_for_scout"]) and one_tick <= 0:
                _log("BLOCK_ENTRY", symbol=symbol, reason="one_tick_not_positive", one_tick=one_tick, strategy=strategy)
                continue

        qty = _safe_float(order.get("quantity") or order.get("qty"))

        if qty > 0 and price > 0:
            max_notional = _safe_float(
                CFG["sideways_scout_notional_usd"]
                if "SIDEWAYS" in regime
                else CFG["trend_scout_notional_usd"]
            )
            notional = qty * price

            if max_notional > 0 and notional > max_notional:
                new_qty = max_notional / price
                order["quantity"] = new_qty
                if "qty" in order:
                    order["qty"] = new_qty
                _log(
                    "RESIZE_ENTRY",
                    symbol=symbol,
                    old_notional=notional,
                    new_notional=max_notional,
                    price=price,
                )

        _record_buy(order, feature, state)
        filtered.append(order)

    return filtered

def _defensive_exit_orders(
    existing_orders: List[Dict[str, Any]],
    ctx: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:

    features = ctx.get("features") or ctx.get("feature_map") or {}
    prices = ctx.get("prices") or ctx.get("market_prices") or ctx.get("tick") or ctx.get("market")

    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()
    if not regime:
        portfolio = ctx.get("portfolio")
        if isinstance(portfolio, dict):
            regime = _safe_str(portfolio.get("regime")).upper()

    positions = _get_positions(ctx)
    exits: List[Dict[str, Any]] = []

    for symbol, pos in positions.items():
        if _has_pending_sell(symbol, existing_orders):
            continue

        feature = _get_feature(features, symbol)
        price = _get_price(symbol, pos, feature, prices)
        qty = _safe_float(pos.get("quantity") or pos.get("qty"))

        if qty <= 0 or price <= 0:
            continue

        meta = _ensure_position_meta(symbol, pos, price, state)
        entry = _safe_float(meta.get("entry_price"))

        if entry <= 0:
            continue

        pnl_pct = (price - entry) / entry
        high_pnl = _safe_float(meta.get("highest_pnl_pct"))
        age_min = max(0.0, (_now_ts() - _safe_float(meta.get("entry_ts"), _now_ts())) / 60.0)
        strategy = _safe_str(meta.get("strategy") or pos.get("strategy"))

        if strategy == "fallback_scout_breakout" and pnl_pct <= _safe_float(CFG["fallback_stop_loss_pct"]):
            exits.append(_make_sell(symbol, qty, price, "adaptive_stop_loss"))
            _record_loss(symbol, "adaptive_stop_loss", state)
            _log("EXIT", symbol=symbol, reason="tight_scout_stop", pnl_pct=pnl_pct, age_min=age_min)
            continue

        if high_pnl >= _safe_float(CFG["breakeven_arm_pct"]) and pnl_pct <= _safe_float(CFG["breakeven_exit_floor_pct"]):
            exits.append(_make_sell(symbol, qty, price, "breakeven_protection_exit"))
            _log("EXIT", symbol=symbol, reason="breakeven_protection", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)
            continue

        if high_pnl >= _safe_float(CFG["trailing_arm_pct"]) and (high_pnl - pnl_pct) >= _safe_float(CFG["trailing_giveback_pct"]):
            exits.append(_make_sell(symbol, qty, price, "trailing_profit_exit"))
            _log("EXIT", symbol=symbol, reason="trailing_profit", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)
            continue

        if "SIDEWAYS" in regime and age_min >= _safe_float(CFG["sideways_time_stop_minutes"]):
            if _safe_float(CFG["sideways_time_stop_min_pnl"]) <= pnl_pct <= _safe_float(CFG["sideways_time_stop_max_pnl"]):
                exits.append(_make_sell(symbol, qty, price, "time_stop_exit"))
                _log("EXIT", symbol=symbol, reason="sideways_time_stop", pnl_pct=pnl_pct, age_min=age_min)
                continue

    return exits

def qfos_expectancy_cycle_guard(proposed_fills: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Defensive expectancy guard.

    It does four things:
    1. Filters weak fallback scout entries.
    2. Reduces SIDEWAYS scout size.
    3. Adds breakeven, trailing, tight-stop, and time-stop sell orders.
    4. Keeps risk_off_exit logic untouched.
    """

    if not CFG.get("enabled", True):
        return proposed_fills if isinstance(proposed_fills, list) else []

    state = _read_state()

    try:
        orders = list(proposed_fills or [])
    except Exception:
        orders = []

    try:
        orders = _filter_and_resize_orders(orders, context, state)
        exits = _defensive_exit_orders(orders, context, state)

        if exits:
            orders = exits + orders

    except Exception as exc:
        _log("ERROR", error=repr(exc))

    _write_state(state)
    return orders

print("QFOS expectancy patch helper loaded.")
