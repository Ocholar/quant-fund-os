LIVE_STATUS_CACHE = {}

def update_live_status_cache(payload: dict):
    try:
        LIVE_STATUS_CACHE.clear()
        LIVE_STATUS_CACHE.update(payload or {})
    except Exception:
        pass

def get_live_status_cache() -> dict:
    return dict(LIVE_STATUS_CACHE)
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
from core.telegram_alerts import send_telegram_alert, send_startup_alert
from data.ingestion import build_market_data
from data.feature_store import FeatureStore
from execution.executor import PaperExecutor, RealMEXCExecutor
from ai.autonomous_agent import AutonomousFundAgent
from services.metrics import trades_total, equity_gauge, drawdown_gauge
from core.control import is_paused, pause_bot, pause_reason
from services.telegram import send_telegram_alert
from fastapi import FastAPI

# QFOS_EXPECTANCY_INLINE_START
# Embedded because Docker image failed to import qfos_expectancy_patch.py.
_QFOS_EXPECTANCY_NS = {"__file__": __file__, "__name__": "_qfos_expectancy_inline"}
exec('\n\nimport json\nimport math\nimport time\nfrom datetime import datetime, timezone\nfrom pathlib import Path\nfrom typing import Any, Dict, List\n\nROOT = Path(__file__).resolve().parent\nCONFIG_PATH = ROOT / "qfos_expectancy_config.json"\n\nDEFAULT_CONFIG = {\n    "enabled": True,\n    "sideways_scout_notional_usd": 1.00,\n    "trend_scout_notional_usd": 1.50,\n    "min_trade_price": 0.01,\n    "min_scout_signal_sideways": 0.004,\n    "min_scout_trend_quality": 0.001,\n    "max_scout_volatility_sideways": 0.006,\n    "require_positive_one_tick_for_scout": True,\n    "fallback_stop_loss_pct": -0.006,\n    "breakeven_arm_pct": 0.0035,\n    "breakeven_exit_floor_pct": 0.0004,\n    "trailing_arm_pct": 0.005,\n    "trailing_giveback_pct": 0.0025,\n    "sideways_time_stop_minutes": 45,\n    "sideways_time_stop_min_pnl": -0.0015,\n    "sideways_time_stop_max_pnl": 0.002,\n    "symbol_cooldown_after_losses": 2,\n    "cooldown_minutes": 180,\n    "state_file": "qfos_expectancy_state.json",\n    "decision_log": "qfos_expectancy_decisions.jsonl",\n}\n\ndef _load_config() -> Dict[str, Any]:\n    try:\n        if CONFIG_PATH.exists():\n            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))\n            merged = dict(DEFAULT_CONFIG)\n            merged.update(data or {})\n            return merged\n    except Exception:\n        pass\n    return dict(DEFAULT_CONFIG)\n\nCFG = _load_config()\nSTATE_PATH = ROOT / str(CFG.get("state_file", "qfos_expectancy_state.json"))\nDECISION_LOG = ROOT / str(CFG.get("decision_log", "qfos_expectancy_decisions.jsonl"))\n\ndef _now_ts() -> float:\n    return time.time()\n\ndef _utc() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\ndef _safe_float(x: Any, default: float = 0.0) -> float:\n    try:\n        if x is None:\n            return default\n        v = float(x)\n        if math.isnan(v) or math.isinf(v):\n            return default\n        return v\n    except Exception:\n        return default\n\ndef _safe_str(x: Any, default: str = "") -> str:\n    try:\n        return str(x)\n    except Exception:\n        return default\n\ndef _read_state() -> Dict[str, Any]:\n    try:\n        if STATE_PATH.exists():\n            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))\n            if isinstance(data, dict):\n                data.setdefault("positions", {})\n                data.setdefault("losses", {})\n                return data\n    except Exception:\n        pass\n    return {"positions": {}, "losses": {}}\n\ndef _write_state(state: Dict[str, Any]) -> None:\n    try:\n        tmp = STATE_PATH.with_suffix(".tmp")\n        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")\n        tmp.replace(STATE_PATH)\n    except Exception:\n        pass\n\ndef _log(action: str, **payload: Any) -> None:\n    try:\n        row = {"ts": _utc(), "action": action}\n        row.update(payload)\n        with DECISION_LOG.open("a", encoding="utf-8") as f:\n            f.write(json.dumps(row, sort_keys=True) + "\\n")\n    except Exception:\n        pass\n\ndef _as_dict(obj: Any) -> Dict[str, Any]:\n    if isinstance(obj, dict):\n        return obj\n\n    data = {}\n    for name in (\n        "symbol", "quantity", "qty", "avg_entry", "entry_price",\n        "mark_price", "price", "strategy", "created_at", "updated_at"\n    ):\n        if hasattr(obj, name):\n            try:\n                data[name] = getattr(obj, name)\n            except Exception:\n                pass\n    return data\n\ndef _get_feature(features: Any, symbol: str) -> Dict[str, Any]:\n    if isinstance(features, dict):\n        value = features.get(symbol, {})\n        if isinstance(value, dict):\n            return value\n    return {}\n\ndef _get_price(symbol: str, pos: Dict[str, Any], feature: Dict[str, Any], prices: Any) -> float:\n    if isinstance(prices, dict) and symbol in prices:\n        p = _safe_float(prices.get(symbol))\n        if p > 0:\n            return p\n\n    p = _safe_float(feature.get("price"))\n    if p > 0:\n        return p\n\n    p = _safe_float(pos.get("mark_price"))\n    if p > 0:\n        return p\n\n    return _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price"))\n\ndef _get_positions(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:\n    raw = (\n        ctx.get("positions")\n        or ctx.get("open_positions")\n        or ctx.get("portfolio_positions")\n        or {}\n    )\n\n    out: Dict[str, Dict[str, Any]] = {}\n\n    if isinstance(raw, dict):\n        for sym, pos in raw.items():\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol") or sym)\n            if symbol:\n                out[symbol] = pd\n\n    elif isinstance(raw, list):\n        for pos in raw:\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol"))\n            if symbol:\n                out[symbol] = pd\n\n    return out\n\ndef _order_symbol(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("symbol"))\n\ndef _order_side(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("side")).lower()\n\ndef _order_strategy(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("strategy") or order.get("reason") or "unknown")\n\ndef _order_price(order: Dict[str, Any], feature: Dict[str, Any]) -> float:\n    p = _safe_float(order.get("fill_price") or order.get("expected_price") or order.get("price"))\n    if p > 0:\n        return p\n    return _safe_float(feature.get("price"))\n\ndef _recent_losses(symbol: str, state: Dict[str, Any], minutes: float) -> int:\n    losses = state.get("losses", {}).get(symbol, [])\n    now = _now_ts()\n    cutoff = now - minutes * 60\n    fresh = [x for x in losses if _safe_float(x.get("ts")) >= cutoff]\n    state.setdefault("losses", {})[symbol] = fresh\n    return len(fresh)\n\ndef _record_loss(symbol: str, strategy: str, state: Dict[str, Any]) -> None:\n    if not symbol:\n        return\n    state.setdefault("losses", {}).setdefault(symbol, []).append({\n        "ts": _now_ts(),\n        "strategy": strategy,\n    })\n\ndef _record_buy(order: Dict[str, Any], feature: Dict[str, Any], state: Dict[str, Any]) -> None:\n    symbol = _order_symbol(order)\n    if not symbol:\n        return\n\n    price = _order_price(order, feature)\n    if price <= 0:\n        return\n\n    state.setdefault("positions", {})[symbol] = {\n        "entry_ts": _now_ts(),\n        "entry_price": price,\n        "highest_price": price,\n        "highest_pnl_pct": 0.0,\n        "strategy": _order_strategy(order),\n        "signal_strength": _safe_float(order.get("signal_strength") or feature.get("signal_strength")),\n    }\n\ndef _ensure_position_meta(symbol: str, pos: Dict[str, Any], price: float, state: Dict[str, Any]) -> Dict[str, Any]:\n    positions = state.setdefault("positions", {})\n    meta = positions.get(symbol)\n\n    entry = _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price") or price)\n\n    if not isinstance(meta, dict):\n        meta = {\n            "entry_ts": _now_ts(),\n            "entry_price": entry if entry > 0 else price,\n            "highest_price": price,\n            "highest_pnl_pct": 0.0,\n            "strategy": _safe_str(pos.get("strategy") or "recovered_position"),\n            "signal_strength": 0.0,\n        }\n        positions[symbol] = meta\n\n    if price > _safe_float(meta.get("highest_price")):\n        meta["highest_price"] = price\n\n    entry_price = _safe_float(meta.get("entry_price") or entry)\n    if entry_price > 0 and price > 0:\n        pnl_pct = (price - entry_price) / entry_price\n        meta["highest_pnl_pct"] = max(_safe_float(meta.get("highest_pnl_pct")), pnl_pct)\n\n    return meta\n\ndef _make_sell(symbol: str, qty: float, price: float, reason: str) -> Dict[str, Any]:\n    return {\n        "symbol": symbol,\n        "side": "sell",\n        "quantity": qty,\n        "expected_price": price,\n        "fill_price": price,\n        "slippage_bps": 0,\n        "strategy": reason,\n        "confidence": 1.0,\n    }\n\ndef _has_pending_sell(symbol: str, orders: List[Dict[str, Any]]) -> bool:\n    for o in orders:\n        if _order_symbol(o) == symbol and _order_side(o) == "sell":\n            return True\n    return False\n\ndef _filter_and_resize_orders(\n    orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    if not orders:\n        return []\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    filtered: List[Dict[str, Any]] = []\n\n    for order in orders:\n        if not isinstance(order, dict):\n            filtered.append(order)\n            continue\n\n        symbol = _order_symbol(order)\n        side = _order_side(order)\n        strategy = _order_strategy(order)\n        feature = _get_feature(features, symbol)\n        price = _order_price(order, feature)\n\n        if side == "sell":\n            if "stop_loss" in strategy:\n                _record_loss(symbol, strategy, state)\n            filtered.append(order)\n            continue\n\n        if side != "buy":\n            filtered.append(order)\n            continue\n\n        is_scout = strategy == "fallback_scout_breakout"\n\n        if not is_scout:\n            _record_buy(order, feature, state)\n            filtered.append(order)\n            continue\n\n        symbol_regime = _safe_str(feature.get("symbol_regime")).upper()\n        signal = _safe_float(order.get("signal_strength") or feature.get("signal_strength"))\n        trend_quality = _safe_float(feature.get("trend_quality") or feature.get("symbol_trend_score"))\n        volatility = _safe_float(feature.get("volatility"))\n        one_tick = _safe_float(feature.get("one_tick_momentum"))\n\n        if price <= 0 or price < _safe_float(CFG["min_trade_price"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="price_too_low", price=price, strategy=strategy)\n            continue\n\n        cooldown_losses = _recent_losses(symbol, state, _safe_float(CFG["cooldown_minutes"]))\n\n        if cooldown_losses >= int(CFG["symbol_cooldown_after_losses"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="loss_cooldown", losses=cooldown_losses, strategy=strategy)\n            continue\n\n        if "SIDEWAYS" in regime:\n            if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="not_clean_uptrend", symbol_regime=symbol_regime, strategy=strategy)\n                continue\n\n            if signal < _safe_float(CFG["min_scout_signal_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_signal", signal=signal, strategy=strategy)\n                continue\n\n            if trend_quality < _safe_float(CFG["min_scout_trend_quality"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_trend_quality", trend_quality=trend_quality, strategy=strategy)\n                continue\n\n            if volatility > _safe_float(CFG["max_scout_volatility_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="extreme_volatility", volatility=volatility, strategy=strategy)\n                continue\n\n            if bool(CFG["require_positive_one_tick_for_scout"]) and one_tick <= 0:\n                _log("BLOCK_ENTRY", symbol=symbol, reason="one_tick_not_positive", one_tick=one_tick, strategy=strategy)\n                continue\n\n        qty = _safe_float(order.get("quantity") or order.get("qty"))\n\n        if qty > 0 and price > 0:\n            max_notional = _safe_float(\n                CFG["sideways_scout_notional_usd"]\n                if "SIDEWAYS" in regime\n                else CFG["trend_scout_notional_usd"]\n            )\n            notional = qty * price\n\n            if max_notional > 0 and notional > max_notional:\n                new_qty = max_notional / price\n                order["quantity"] = new_qty\n                if "qty" in order:\n                    order["qty"] = new_qty\n                _log(\n                    "RESIZE_ENTRY",\n                    symbol=symbol,\n                    old_notional=notional,\n                    new_notional=max_notional,\n                    price=price,\n                )\n\n        _record_buy(order, feature, state)\n        filtered.append(order)\n\n    return filtered\n\ndef _defensive_exit_orders(\n    existing_orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n    prices = ctx.get("prices") or ctx.get("market_prices") or ctx.get("tick") or ctx.get("market")\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    positions = _get_positions(ctx)\n    exits: List[Dict[str, Any]] = []\n\n    for symbol, pos in positions.items():\n        if _has_pending_sell(symbol, existing_orders):\n            continue\n\n        feature = _get_feature(features, symbol)\n        price = _get_price(symbol, pos, feature, prices)\n        qty = _safe_float(pos.get("quantity") or pos.get("qty"))\n\n        if qty <= 0 or price <= 0:\n            continue\n\n        meta = _ensure_position_meta(symbol, pos, price, state)\n        entry = _safe_float(meta.get("entry_price"))\n\n        if entry <= 0:\n            continue\n\n        pnl_pct = (price - entry) / entry\n        high_pnl = _safe_float(meta.get("highest_pnl_pct"))\n        age_min = max(0.0, (_now_ts() - _safe_float(meta.get("entry_ts"), _now_ts())) / 60.0)\n        strategy = _safe_str(meta.get("strategy") or pos.get("strategy"))\n\n        if strategy == "fallback_scout_breakout" and pnl_pct <= _safe_float(CFG["fallback_stop_loss_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "adaptive_stop_loss"))\n            _record_loss(symbol, "adaptive_stop_loss", state)\n            _log("EXIT", symbol=symbol, reason="tight_scout_stop", pnl_pct=pnl_pct, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["breakeven_arm_pct"]) and pnl_pct <= _safe_float(CFG["breakeven_exit_floor_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "breakeven_protection_exit"))\n            _log("EXIT", symbol=symbol, reason="breakeven_protection", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["trailing_arm_pct"]) and (high_pnl - pnl_pct) >= _safe_float(CFG["trailing_giveback_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "trailing_profit_exit"))\n            _log("EXIT", symbol=symbol, reason="trailing_profit", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if "SIDEWAYS" in regime and age_min >= _safe_float(CFG["sideways_time_stop_minutes"]):\n            if _safe_float(CFG["sideways_time_stop_min_pnl"]) <= pnl_pct <= _safe_float(CFG["sideways_time_stop_max_pnl"]):\n                exits.append(_make_sell(symbol, qty, price, "time_stop_exit"))\n                _log("EXIT", symbol=symbol, reason="sideways_time_stop", pnl_pct=pnl_pct, age_min=age_min)\n                continue\n\n    return exits\n\ndef qfos_expectancy_cycle_guard(proposed_fills: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:\n    """\n    Defensive expectancy guard.\n\n    It does four things:\n    1. Filters weak fallback scout entries.\n    2. Reduces SIDEWAYS scout size.\n    3. Adds breakeven, trailing, tight-stop, and time-stop sell orders.\n    4. Keeps risk_off_exit logic untouched.\n    """\n\n    if not CFG.get("enabled", True):\n        return proposed_fills if isinstance(proposed_fills, list) else []\n\n    state = _read_state()\n\n    try:\n        orders = list(proposed_fills or [])\n    except Exception:\n        orders = []\n\n    try:\n        orders = _filter_and_resize_orders(orders, context, state)\n        exits = _defensive_exit_orders(orders, context, state)\n\n        if exits:\n            orders = exits + orders\n\n    except Exception as exc:\n        _log("ERROR", error=repr(exc))\n\n    _write_state(state)\n    return orders\n\nprint("QFOS expectancy patch helper loaded.")\n', _QFOS_EXPECTANCY_NS)
qfos_expectancy_cycle_guard = _QFOS_EXPECTANCY_NS["qfos_expectancy_cycle_guard"]

# QFOS_EXPECTANCY_EARLY_HOOK_START
def _qfos_expectancy_guard_with_cycle_log_inner(proposed_fills=None, context=None):
    """
    Runs qfos_expectancy_cycle_guard and logs every cycle, even when no
    entry/exit/block/resize decision is made.

    This wrapper exists so we can prove the guard is active before final
    execution instead of guessing from missing jsonl logs.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    _decision_log = _root / "qfos_expectancy_decisions.jsonl"

    try:
        _decision_log.touch(exist_ok=True)
    except Exception:
        pass

    try:
        _before_orders = list(proposed_fills or [])
    except Exception:
        _before_orders = []

    _before = len(_before_orders)

    try:
        _after_orders = qfos_expectancy_cycle_guard(_before_orders, context or {})
        if _after_orders is None:
            _after_orders = []
        if not isinstance(_after_orders, list):
            _after_orders = list(_after_orders or [])
    except Exception as _exc:
        print(f"[EXPECTANCY_PATCH] guard failed: {_exc}")
        _after_orders = _before_orders
        try:
            with _decision_log.open("a", encoding="utf-8") as _f:
                _f.write(_json.dumps({
                    "ts": _dt.now(_tz.utc).isoformat(),
                    "action": "ERROR",
                    "where": "early_hook_wrapper",
                    "error": repr(_exc),
                    "before": _before,
                    "after": len(_after_orders),
                    "exits": 0,
                }, sort_keys=True) + "\n")
        except Exception:
            pass
        return _after_orders

    _after = len(_after_orders)

    _exit_reasons = {
        "adaptive_stop_loss",
        "stop_loss",
        "stop_loss_exit",
        "adaptive_take_profit",
        "take_profit",
        "trailing_profit_exit",
        "breakeven_protection_exit",
        "time_stop_exit",
        "risk_off_exit",
        "emergency_exposure_reduction",
    }

    _exits = 0
    for _o in _after_orders:
        if isinstance(_o, dict):
            _side = str(_o.get("side", "")).lower()
            _strategy = str(_o.get("strategy") or _o.get("reason") or "").lower()
            if _side == "sell" or _strategy in _exit_reasons or _strategy.endswith("_exit"):
                _exits += 1

    _line = f"[EXPECTANCY_PATCH] before={_before} after={_after} exits={_exits}"
    print(_line)

    try:
        with _decision_log.open("a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "ts": _dt.now(_tz.utc).isoformat(),
                "action": "CYCLE",
                "before": _before,
                "after": _after,
                "exits": _exits,
            }, sort_keys=True) + "\n")
    except Exception:
        pass

    return _after_orders


def qfos_expectancy_guard_with_cycle_log(proposed_fills=None, context=None):
    """
    Safe expectancy wrapper.
    Always:
      - avoids unbound proposed_fills crashes
      - calls the original expectancy guard if available
      - prints [EXPECTANCY_PATCH] before/after/exits every cycle
      - appends one JSONL row every cycle, even when no trade is changed
    """
    import json as _json
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    before_list = list(proposed_fills or [])
    after_list = before_list
    error = None

    try:
        if "_qfos_expectancy_guard_with_cycle_log_inner" in globals():
            guarded = _qfos_expectancy_guard_with_cycle_log_inner(before_list, context)
            after_list = list(guarded or [])
        elif "qfos_expectancy_cycle_guard" in globals():
            guarded = qfos_expectancy_cycle_guard(before_list, context)
            after_list = list(guarded or [])
        else:
            after_list = before_list
    except Exception as exc:
        error = repr(exc)
        after_list = before_list

    before = len(before_list)
    after = len(after_list)
    exits = max(0, before - after)

    line = f"[EXPECTANCY_PATCH] before={before} after={after} exits={exits}"
    if error:
        line += f" error={error}"
    print(line)

    try:
        row = {
            "ts": _dt.utcnow().isoformat() + "Z",
            "before": before,
            "after": after,
            "exits": exits,
            "error": error,
            "symbols_before": [x.get("symbol") for x in before_list if isinstance(x, dict)],
            "symbols_after": [x.get("symbol") for x in after_list if isinstance(x, dict)],
        }
        with _Path("qfos_expectancy_decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(_json.dumps(row, sort_keys=True) + "\n")
    except Exception as log_exc:
        print("[EXPECTANCY_PATCH] jsonl write failed: " + repr(log_exc))

    return after_list
# QFOS_EXPECTANCY_EARLY_HOOK_END

# QFOS_EXPECTANCY_INLINE_END


app = FastAPI(title="Quant Fund OS")

def _winning_strategy_get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _winning_strategy_pnl_pct(position):
    try:
        entry = float(_winning_strategy_get(position, "entry_price", 0) or 0)
        mark = float(
            _winning_strategy_get(position, "mark_price", None)
            or _winning_strategy_get(position, "price", 0)
            or 0
        )
        if entry <= 0:
            return 0.0
        return (mark - entry) / entry
    except Exception:
        return 0.0

def _winning_strategy_log(action, reason, symbol, pnl_pct):
    try:
        import json
        with open("winning_strategy_decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "action": action,
                "reason": reason,
                "symbol": str(symbol),
                "pnl_pct": float(pnl_pct),
            }) + "\n")
    except Exception:
        pass

def allow_risk_off_exit(position, global_state=None, portfolio=None, reason=""):
    """
    Winning Strategy Guard.
    True  = allow old risk_off_exit sell.
    False = suppress premature panic sell.
    """
    regime = str(_winning_strategy_get(global_state, "regime", "") or "").upper()
    symbol = _winning_strategy_get(position, "symbol", "UNKNOWN")
    pnl_pct = _winning_strategy_pnl_pct(position)

    # Never block real emergency exits.
    if "HARD" in regime or "EMERGENCY" in str(reason).upper():
        return True

    # During soft RISK_OFF, do not panic-sell winners or flat positions.
    if "RISK_OFF" in regime and pnl_pct >= 0:
        _winning_strategy_log(
            "HOLD",
            "profit_shield_suppressed_risk_off_exit",
            symbol,
            pnl_pct,
        )
        return False

    # If only slightly down, let local stop-loss handle it instead of global panic sell.
    if "RISK_OFF" in regime and pnl_pct > -0.003:
        _winning_strategy_log(
            "TIGHT_TRAIL",
            "minor_loss_suppressed_risk_off_exit",
            symbol,
            pnl_pct,
        )
        return False

    return True




# ============================================================
# QFOS CLEAN INTEGRATED STRATEGY LAYER
#
# Design:
# - No external qfos_winning_strategy import.
# - Risk-off sells are gated by allow_risk_off_exit.
# - Scout fallback creates a NORMAL fill dictionary only.
# - The main loop still applies entry_policy_allows -> can_buy -> apply_buy.
# - Scout fallback is blocked during corrupted market-data cycles.
# ============================================================

QFOS_SCOUT_FALLBACK_ENABLED = True
QFOS_SCOUT_MAX_VALUE_USD = float(getattr(settings, "qfos_scout_max_value_usd", 2.00))
QFOS_SCOUT_MIN_VALUE_USD = float(getattr(settings, "qfos_scout_min_value_usd", 1.00))
QFOS_SCOUT_MAX_EXPOSURE_PCT = float(getattr(settings, "qfos_scout_max_exposure_pct", 0.08))
QFOS_SCOUT_MIN_SIGNAL = float(getattr(settings, "qfos_scout_min_signal", 0.0010))
QFOS_SCOUT_CONFIDENCE = float(getattr(settings, "qfos_scout_confidence", 0.78))
QFOS_SCOUT_ALLOW_RISK_OFF = str(getattr(settings, "qfos_scout_allow_risk_off", "true")).lower() in ("1", "true", "yes", "on")
QFOS_SCOUT_MIN_PRICE = float(getattr(settings, "qfos_scout_min_price", 0.001))
QFOS_SCOUT_MAX_VOLATILITY = float(getattr(settings, "qfos_scout_max_volatility", 0.012))
QFOS_SCOUT_MIN_SIDEWAYS_SIGNAL = float(getattr(settings, "qfos_scout_min_sideways_signal", 0.0060))
QFOS_SCOUT_MIN_TREND_QUALITY = float(getattr(settings, "qfos_scout_min_trend_quality", 0.0030))
QFOS_SCOUT_RECENT_STOP_LOSS_HOURS = float(getattr(settings, "qfos_scout_recent_stop_loss_hours", 2))

LAST_MARKET_DATA_HEALTH = {
    "sane_for_entries": False,
    "reject_count": 0,
    "large_tick_count": 0,
    "trusted_count": 0,
    "total_count": 0,
    "reason": "not_initialized",
}

def _qfos_obj_get(obj, key, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default

def _qfos_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

def qfos_market_data_sane_for_entries(prices=None):
    """
    Block new fallback entries when the feed is suspicious.
    Normal exits may still run using validated/last-good prices.
    """
    try:
        h = globals().get("LAST_MARKET_DATA_HEALTH", {}) or {}
        if not h.get("sane_for_entries", False):
            print(f"[MARKET_SANITY] entries blocked: {h}")
            return False
        if not isinstance(prices, dict) or len(prices) < 20:
            print("[MARKET_SANITY] entries blocked: insufficient validated prices")
            return False
        for stable in ("USDC/USDT", "USD1/USDT"):
            if stable in prices:
                px = _qfos_float(prices.get(stable), 1.0)
                if px < 0.95 or px > 1.05:
                    print(f"[MARKET_SANITY] entries blocked: bad stablecoin {stable}={px}")
                    return False
        return True
    except Exception as e:
        print(f"[MARKET_SANITY] entries blocked: sanity error {e}")
        return False

def _qfos_current_positions():
    try:
        pos = getattr(portfolio, "positions", {})
        return pos if isinstance(pos, dict) else {}
    except Exception:
        return {}

def _qfos_cash():
    try:
        return float(getattr(portfolio, "cash", 0.0) or 0.0)
    except Exception:
        return 0.0

def _qfos_equity_from_prices(prices):
    try:
        eq = float(portfolio.mark_to_market(prices or {}) or 0.0)
        return eq if eq > 0 else _qfos_cash()
    except Exception:
        return _qfos_cash()

def _qfos_exposure_pct(prices):
    try:
        equity = _qfos_equity_from_prices(prices or {})
        if equity <= 0:
            return 0.0
        exposure = 0.0
        for sym, qty in _qfos_current_positions().items():
            exposure += abs(_qfos_float(qty)) * _qfos_float((prices or {}).get(sym))
        return exposure / equity
    except Exception:
        return 0.0

def _qfos_is_excluded_entry_symbol(symbol):
    s = str(symbol or "").upper()
    return s in {"USDC/USDT", "USD1/USDT", "EUR/USDT", "GOLD(PAXG)/USDT"}

def _qfos_symbol_db_quarantined(symbol):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT symbol
                    FROM symbol_quarantine
                    WHERE symbol = :symbol
                      AND blocked_until IS NOT NULL
                      AND blocked_until > DATETIME('now', '+3 hours')
                    LIMIT 1
                """),
                {"symbol": symbol},
            ).first()
        return row is not None
    except Exception as e:
        print(f"[SCOUT_FALLBACK] quarantine check error {symbol}: {e}")
        return True

def _qfos_symbol_recent_stop_loss(symbol):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT COUNT(*) AS count
                    FROM trades
                    WHERE symbol = :symbol
                      AND side = 'sell'
                      AND (
                            strategy IN ('stop_loss', 'adaptive_stop_loss')
                         OR strategy LIKE '%stop_loss%'
                      )
                      AND created_at >= DATETIME('now', '+3 hours', '-' || :hours || ' hours')
                """),
                {"symbol": symbol, "hours": QFOS_SCOUT_RECENT_STOP_LOSS_HOURS},
            ).mappings().first()
        return int(row["count"] or 0) > 0 if row else False
    except Exception as e:
        print(f"[SCOUT_FALLBACK] recent SL check error {symbol}: {e}")
        return True


def _qfos_recent_scout_stop_loss_count(hours=1.5):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT COUNT(*) AS count
                    FROM trades
                    WHERE side = 'sell'
                      AND (
                            strategy IN ('stop_loss', 'adaptive_stop_loss')
                         OR strategy LIKE '%stop_loss%'
                      )
                      AND created_at >= DATETIME('now', '+3 hours', '-' || :hours || ' hours')
                """),
                {"hours": hours},
            ).mappings().first()
        return int(row["count"] or 0) if row else 0
    except Exception as e:
        print(f"[SCOUT_FALLBACK] recent global SL check error: {e}")
        return 99

def _qfos_scout_global_cooldown_active():
    recent_sl = _qfos_recent_scout_stop_loss_count(hours=1.5)
    if recent_sl >= 3:
        print(f"[SCOUT_FALLBACK] skipped: global stop-loss cooldown recent_sl={recent_sl}")
        return True
    return False


def _qfos_symbol_blocked_for_scout(symbol):
    if _qfos_symbol_db_quarantined(symbol):
        print(f"[SCOUT_FALLBACK] skip {symbol}: db_quarantined")
        return True
    if _qfos_symbol_recent_stop_loss(symbol):
        print(f"[SCOUT_FALLBACK] skip {symbol}: recent_stop_loss")
        return True
    return False


def _qfos_scout_score(feature):
    signal = _qfos_float(feature.get("signal_strength"))
    trend_quality = _qfos_float(feature.get("trend_quality"))
    breakout = _qfos_float(feature.get("breakout_score"))
    momentum = _qfos_float(feature.get("momentum"))
    one_tick = _qfos_float(feature.get("one_tick_momentum"))
    trend = _qfos_float(feature.get("trend"))
    volatility = abs(_qfos_float(feature.get("volatility")))

    # Reward strong trend/breakout and positive momentum; lightly penalize extreme volatility.
    return (
        signal * 100.0
        + trend_quality * 150.0
        + breakout * 100.0
        + max(momentum, 0.0) * 40.0
        + max(one_tick, 0.0) * 20.0
        + max(trend, 0.0) * 25.0
        - max(volatility - 0.02, 0.0) * 10.0
    )

def _qfos_feature_clean_uptrend(feature, regime=None):
    """
    Higher-quality scout filter.

    Goal:
    - Do not buy quarantined/recent-stop-loss symbols.
    - Avoid fake breakouts caused by noisy/corrupted history.
    - Avoid ultra-micro-price symbols.
    - Require actual trend quality, not only one noisy tick.
    """
    if not isinstance(feature, dict):
        return False
    if not bool(feature.get("ready", True)):
        return False
    if str(feature.get("source", "NORMAL")).upper() == "RAW_MOMENTUM_FALLBACK":
        return False

    symbol_regime = str(feature.get("symbol_regime", "") or "").upper()
    is_up = bool(feature.get("is_symbol_uptrend", False))
    is_down = bool(feature.get("is_symbol_downtrend", False))
    is_choppy = bool(feature.get("is_choppy", False))

    signal = _qfos_float(feature.get("signal_strength"))
    trend = _qfos_float(feature.get("trend"))
    long_trend = _qfos_float(feature.get("long_trend"))
    momentum = _qfos_float(feature.get("momentum"))
    one_tick = _qfos_float(feature.get("one_tick_momentum"))
    volatility = abs(_qfos_float(feature.get("volatility")))
    trend_quality = _qfos_float(feature.get("trend_quality"))
    breakout_score = _qfos_float(feature.get("breakout_score"))

    if is_down or is_choppy:
        return False

    if symbol_regime not in {"SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"} and not is_up:
        return False

    min_signal = QFOS_SCOUT_MIN_SIGNAL
    if str(regime or "").upper() == "SIDEWAYS":
        min_signal = max(min_signal, QFOS_SCOUT_MIN_SIDEWAYS_SIGNAL)

    if signal < min_signal:
        return False

    if volatility > QFOS_SCOUT_MAX_VOLATILITY and signal < 0.02:
        return False

    if max(trend_quality, breakout_score) < QFOS_SCOUT_MIN_TREND_QUALITY:
        return False

    # In SIDEWAYS, demand cleaner agreement.
    r = str(regime or "").upper()

    if r == "SIDEWAYS":
        if long_trend <= 0:
            return False
        if trend <= 0:
            return False
        if momentum <= 0:
            return False
        if one_tick < -0.0002:
            return False
    else:
        confirmations = 0
        confirmations += 1 if trend > 0 else 0
        confirmations += 1 if momentum > 0 else 0
        confirmations += 1 if one_tick >= -0.0005 else 0
        confirmations += 1 if long_trend >= 0 else 0

        if confirmations < 2:
            return False

    return True

def qfos_select_scout_candidate(feature_map, prices, regime=None):
    candidates = []
    current_positions = _qfos_current_positions()

    for symbol, feature in (feature_map or {}).items():
        try:
            if _qfos_is_excluded_entry_symbol(symbol):
                continue

            if _qfos_symbol_blocked_for_scout(symbol):
                continue

            if _qfos_float(current_positions.get(symbol)) > 0:
                continue

            price = _qfos_float((prices or {}).get(symbol) or (feature or {}).get("price"))
            if price <= 0:
                continue

            if price < QFOS_SCOUT_MIN_PRICE:
                print(f"[SCOUT_FALLBACK] skip {symbol}: price_too_low {price}")
                continue

            if not _qfos_feature_clean_uptrend(feature, regime=regime):
                continue

            score = _qfos_scout_score(feature)
            candidates.append((score, symbol, price, feature))
        except Exception as e:
            print(f"[SCOUT_FALLBACK] candidate error {symbol}: {e}")
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0] if candidates else None

def qfos_build_scout_fallback_order(feature_map, prices, regime, equity, cash):
    """
    Return one properly-shaped buy fill dict, or None.
    This function does NOT mutate the portfolio and does NOT call apply_buy.
    """
    try:
        if not QFOS_SCOUT_FALLBACK_ENABLED:
            return None
        if not qfos_market_data_sane_for_entries(prices):
            return None

        r = str(regime or "").upper()
        if r in {"RISK_OFF_HARD", "BLOCKED"}:
            print("[SCOUT_FALLBACK] skipped: hard risk regime")
            return None
        if r == "RISK_OFF" and not QFOS_SCOUT_ALLOW_RISK_OFF:
            print("[SCOUT_FALLBACK] skipped: risk_off entries disabled")
            return None

        exposure_pct = _qfos_exposure_pct(prices or {})
        if exposure_pct >= QFOS_SCOUT_MAX_EXPOSURE_PCT:
            print(f"[SCOUT_FALLBACK] skipped: exposure cap reached {exposure_pct:.4f}")
            return None

        candidate = qfos_select_scout_candidate(feature_map or {}, prices or {}, regime=regime)
        if not candidate:
            print("[SCOUT_FALLBACK] skipped: no clean uptrend/breakout candidate")
            return None

        score, symbol, price, feature = candidate
        cash = _qfos_float(cash)
        equity = _qfos_float(equity)
        if cash < QFOS_SCOUT_MIN_VALUE_USD:
            print(f"[SCOUT_FALLBACK] skipped: insufficient cash {cash:.2f}")
            return None

        value = min(QFOS_SCOUT_MAX_VALUE_USD, max(QFOS_SCOUT_MIN_VALUE_USD, equity * 0.015))
        value = min(value, cash * 0.50)
        qty = value / price if price > 0 else 0.0
        if qty <= 0:
            return None

        signal = _qfos_float(feature.get("signal_strength"))
        confidence = max(QFOS_SCOUT_CONFIDENCE, min(0.95, signal))
        order = {
            "symbol": symbol,
            "side": "buy",
            "quantity": qty,
            "expected_price": price,
            "fill_price": price,
            "slippage_bps": 0,
            "strategy": "fallback_scout_breakout",
            "confidence": confidence,
            "signal_strength": signal,
            "feature": feature,
        }
        print(
            f"[SCOUT_FALLBACK] SELECTED {symbol} qty={qty:.8f} "
            f"value=${value:.2f} price={price} score={score:.4f} "
            f"regime={feature.get('symbol_regime')} signal={signal:.5f}"
        )
        return order
    except Exception as e:
        print(f"[SCOUT_FALLBACK] error: {e}")
        return None


EXIT_REASON_STRATEGIES = {'stop_loss', 'stop_loss_exit', 'adaptive_stop_loss', 'take_profit', 'adaptive_take_profit', 'risk_off_exit', 'emergency_exposure_reduction', 'breakeven_protection_exit', 'time_stop_exit', 'trailing_profit_exit'}

def normalize_exit_order(order):
    if not isinstance(order, dict):
        return order
    side = str(order.get('side') or '').lower()
    strategy = str(order.get('strategy') or '').strip()
    if side == 'sell' and strategy.lower() in EXIT_REASON_STRATEGIES:
        order.setdefault('exit_reason', strategy.lower())
        order.setdefault('raw_strategy', strategy)
        order.setdefault('display_strategy', strategy.lower())
    else:
        order.setdefault('entry_strategy', strategy)
        order.setdefault('display_strategy', strategy)
    return order
INITIAL_EQUITY = float(settings.starting_equity)
MAX_TOTAL_EXPOSURE_PCT = float(settings.max_total_exposure_pct)
MAX_SYMBOL_EXPOSURE_PCT = float(settings.max_symbol_exposure_pct)
MAX_TRADES_PER_SYMBOL = int(settings.max_trades_per_symbol)
ENTRY_QUALITY_LOCKDOWN_ENABLED = True
ENTRY_QUALITY_TOP_N = int(getattr(settings, 'entry_quality_top_n', 2))
ENTRY_MIN_SIGNAL_SIDEWAYS = float(getattr(settings, 'entry_min_signal_sideways', 0.025))
ENTRY_MIN_SIGNAL_TRENDING = float(getattr(settings, 'entry_min_signal_trending', 0.018))
ENTRY_MAX_VOLATILITY = float(getattr(settings, 'entry_max_volatility', 0.008))
ENTRY_MIN_EXPECTED_MOVE_PCT = float(getattr(settings, 'entry_min_expected_move_pct', 0.012))
ENTRY_STOP_LOSS_QUARANTINE_HOURS = float(getattr(settings, 'entry_stop_loss_quarantine_hours', 4))
ENTRY_REQUIRE_TRIPLE_AGREEMENT = str(getattr(settings, 'entry_require_triple_agreement', 'true')).lower() in ('1', 'true', 'yes', 'on')
ENTRY_BLOCK_SIDEWAYS_IF_NO_TOP_CANDIDATE = True
SAME_SYMBOL_ENTRY_COOLDOWN_MINUTES = float(getattr(settings, 'same_symbol_entry_cooldown_minutes', 30))
SAME_SYMBOL_EXCEPTIONAL_COOLDOWN_MINUTES = float(getattr(settings, 'same_symbol_exceptional_cooldown_minutes', 10))
ENTRY_QUALITY_LOG_REJECTION_LIMIT = int(getattr(settings, 'entry_quality_log_rejection_limit', 12))
SIDEWAYS_ENTRY_MIN_GAP_MINUTES = float(getattr(settings, 'sideways_entry_min_gap_minutes', 15))
SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE = int(getattr(settings, 'sideways_reserve_final_slot_until_minute', 35))
SIDEWAYS_EXCEPTIONAL_SIGNAL = float(getattr(settings, 'sideways_exceptional_signal', 0.045))
SIDEWAYS_EXCEPTIONAL_LADDER = [float(x.strip()) for x in str(getattr(settings, 'sideways_exceptional_ladder', '0.045,0.050,0.055,0.060,0.065,0.070')).split(',') if x.strip()]
SIDEWAYS_EXCEPTIONAL_BYPASS_HOURLY_CAP = str(getattr(settings, 'sideways_exceptional_bypass_hourly_cap', 'true')).lower() in ('1', 'true', 'yes', 'on')
SIDEWAYS_EXCEPTIONAL_BYPASS_PACING = str(getattr(settings, 'sideways_exceptional_bypass_pacing', 'true')).lower() in ('1', 'true', 'yes', 'on')
ENTRY_REQUIRE_LONG_TREND = str(getattr(settings, 'entry_require_long_trend', 'true')).lower() in ('1', 'true', 'yes', 'on')
EXCLUDED_ENTRY_SYMBOLS = {'USDC/USDT', 'USD1/USDT', 'EUR/USDT', 'GOLD(PAXG)/USDT', 'MOGU/USDT', 'SHIB/USDT', 'AIXDROP/USDT'}
TRADE_COUNT_WINDOW_HOURS = float(getattr(settings, 'trade_count_window_hours', 4))
STOP_LOSS_PCT = float(settings.stop_loss_pct)
TAKE_PROFIT_PCT = float(settings.take_profit_pct)
FULL_TAKE_PROFIT_PCT = float(getattr(settings, 'full_take_profit_pct', 0.008))
BREAKEVEN_TRIGGER_PCT = float(getattr(settings, 'breakeven_trigger_pct', 0.004))
BREAKEVEN_EXIT_PCT = float(getattr(settings, 'breakeven_exit_pct', 0.0008))
TIME_STOP_MINUTES = float(getattr(settings, 'position_time_stop_minutes', 40))
TIME_STOP_EXIT_BELOW_PCT = float(getattr(settings, 'time_stop_exit_below_pct', 0.0015))
WIN_RATE_TRAIL_TRIGGER_PCT = float(getattr(settings, 'win_rate_trail_trigger_pct', 0.007))
WIN_RATE_TRAIL_GIVEBACK_PCT = float(getattr(settings, 'win_rate_trail_giveback_pct', 0.0035))
WIN_RATE_SIDEWAYS_FULL_TP_PCT = float(getattr(settings, 'win_rate_sideways_full_tp_pct', 0.0075))
WIN_RATE_MIN_HOLD_BEFORE_BREAKEVEN_MIN = float(getattr(settings, 'win_rate_min_hold_before_breakeven_min', 8))

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
last_known_regime = 'UNKNOWN'
last_seen_paused_state = None
ALLOW_BUYS = True
ALLOW_SELLS = True

# Runtime risk status fallback used by the bot loop.
risk_status = 'SAFE'

EXCLUDED_TRADING_SYMBOLS = {'USDC/USDT', 'USD1/USDT', 'EUR/USDT', 'GOLD(PAXG)/USDT', 'MOGU/USDT', 'SHIB/USDT', 'AIXDROP/USDT'}
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
    print('Recovering state from database...')
    try:
        with engine.begin() as conn:
            snap = conn.execute(text('\n                SELECT cash, equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1\n            ')).mappings().first()
            if snap:
                recovered_cash = float(snap['cash'])
                recovered_equity = float(snap['equity'] or INITIAL_EQUITY)
                if recovered_cash < -0.01 or recovered_equity > INITIAL_EQUITY * 5:
                    msg = f'state_corruption_detected cash={recovered_cash:.2f} equity={recovered_equity:.2f}; reset quant.db before continuing'
                    print('WARNING:', msg)
                    pause_bot(msg)
                    portfolio.cash = max(0.0, min(recovered_cash, INITIAL_EQUITY))
                    portfolio.peak = INITIAL_EQUITY
                else:
                    portfolio.cash = recovered_cash
                    portfolio.peak = max(portfolio.peak, recovered_equity)
                print(f'Recovered cash: ${portfolio.cash:.2f}')
            rows = conn.execute(text('\n                SELECT symbol, quantity, avg_entry FROM positions WHERE quantity > 0\n            ')).mappings().all()
            for r in rows:
                portfolio.positions[r['symbol']] = float(r['quantity'])
                entry_prices[r['symbol']] = float(r['avg_entry'])
            if rows:
                print(f'Recovered {len(rows)} open positions.')
            trades = conn.execute(text("\n                SELECT symbol, created_at\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')\n            "), {'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().all()
            for t in trades:
                sym = t['symbol']
                trade_counts[sym] = trade_counts.get(sym, 0) + 1
                try:
                    dt = datetime.fromisoformat(str(t['created_at']))
                    ts = dt.timestamp()
                    last_trade_time[sym] = max(last_trade_time.get(sym, 0), ts)
                except:
                    pass
    except Exception as e:
        print(f'State recovery failed: {e}')
print('Quant Fund OS starting. LIVE_TRADING=', settings.live_trading)
print('Safety mode enabled. Paper trading only.')
try:
    send_startup_alert()
except Exception as e:
    print('Startup Telegram alert failed:', e)
send_telegram_alert('Quant Fund OS started. Paper mode active. Live trading is OFF.')
last_risk_status = None

def wait_for_database(max_attempts=30):
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.begin() as conn:
                conn.execute(text('SELECT 1'))
            print('Database connected.')
            return
        except OperationalError:
            print(f'Waiting for database... attempt {attempt}/{max_attempts}')
            time.sleep(2)
    raise RuntimeError('Database was not ready after waiting.')
LAST_GOOD_PRICES = {}
PENDING_PRICES = {}
BAD_PRICE_TICKS = {}

def validate_market_prices(raw_prices):
    """
    Market data integrity layer.

    Rules:
    - First tick is pending, not trusted.
    - A symbol becomes trusted after two reasonably close ticks.
    - Extreme discontinuities are rejected and last_good is reused.
    - Large discontinuities are NOT accepted immediately; last_good is reused and
      the cycle is marked unhealthy for new entries.
    - New buys/scout fallback are blocked when the cycle has too many bad ticks.
    """
    global LAST_MARKET_DATA_HEALTH

    clean = {}
    reject_count = 0
    large_tick_count = 0
    trusted_count = 0
    total_count = len(raw_prices) if isinstance(raw_prices, dict) else 0

    if not isinstance(raw_prices, dict):
        LAST_MARKET_DATA_HEALTH = {
            "sane_for_entries": False,
            "reject_count": 0,
            "large_tick_count": 0,
            "trusted_count": 0,
            "total_count": 0,
            "reason": "raw_prices_not_dict",
        }
        print("PRICE VALIDATION BLOCK: raw_prices_not_dict")
        return clean

    for symbol, raw_price in raw_prices.items():
        try:
            price = float(raw_price or 0)
        except Exception:
            reject_count += 1
            BAD_PRICE_TICKS[symbol] = BAD_PRICE_TICKS.get(symbol, 0) + 1
            print(f"PRICE VALIDATION REJECT {symbol}: non_numeric price={raw_price}")
            continue

        if price <= 0:
            reject_count += 1
            BAD_PRICE_TICKS[symbol] = BAD_PRICE_TICKS.get(symbol, 0) + 1
            print(f"PRICE VALIDATION REJECT {symbol}: non_positive price={price}")
            continue

        last_good = LAST_GOOD_PRICES.get(symbol)

        if not last_good:
            pending = PENDING_PRICES.get(symbol)
            if not pending:
                PENDING_PRICES[symbol] = price
                print(f"PRICE VALIDATION PENDING {symbol}: first_seen price={price:.12f}")
                continue

            ratio = price / pending if pending else 0
            if ratio > 3.0 or ratio < 0.333:
                reject_count += 1
                print(
                    f"PRICE VALIDATION RESET_PENDING {symbol}: "
                    f"new_price={price:.12f} old_pending={pending:.12f} ratio={ratio:.6f}"
                )
                PENDING_PRICES[symbol] = price
                continue

            LAST_GOOD_PRICES[symbol] = price
            clean[symbol] = price
            trusted_count += 1
            print(f"PRICE VALIDATION TRUSTED {symbol}: price={price:.12f}")
            continue

        ratio = price / last_good if last_good else 0

        if ratio > 3.0 or ratio < 0.333:
            reject_count += 1
            BAD_PRICE_TICKS[symbol] = BAD_PRICE_TICKS.get(symbol, 0) + 1
            print(
                f"PRICE VALIDATION REJECT {symbol}: extreme_tick "
                f"price={price:.12f} last_good={last_good:.12f} "
                f"ratio={ratio:.6f} bad_count={BAD_PRICE_TICKS[symbol]}"
            )
            clean[symbol] = last_good
            continue

        if ratio > 1.35 or ratio < 0.65:
            large_tick_count += 1
            BAD_PRICE_TICKS[symbol] = BAD_PRICE_TICKS.get(symbol, 0) + 1
            print(
                f"PRICE VALIDATION HOLD_LAST_GOOD {symbol}: large_tick "
                f"price={price:.12f} last_good={last_good:.12f} "
                f"ratio={ratio:.6f} bad_count={BAD_PRICE_TICKS[symbol]}"
            )
            clean[symbol] = last_good
            continue

        LAST_GOOD_PRICES[symbol] = price
        clean[symbol] = price
        trusted_count += 1
        if BAD_PRICE_TICKS.get(symbol, 0) > 0:
            BAD_PRICE_TICKS[symbol] = max(0, BAD_PRICE_TICKS.get(symbol, 0) - 1)

    bad_total = reject_count + large_tick_count
    sane_for_entries = (
        len(clean) >= 20
        and bad_total <= max(3, int(total_count * 0.10))
        and _qfos_float(clean.get("USDC/USDT", 1.0), 1.0) <= 1.05
        and _qfos_float(clean.get("USDC/USDT", 1.0), 1.0) >= 0.95
        and _qfos_float(clean.get("USD1/USDT", 1.0), 1.0) <= 1.05
        and _qfos_float(clean.get("USD1/USDT", 1.0), 1.0) >= 0.95
    )
    reason = "ok" if sane_for_entries else f"bad_ticks_{bad_total}_of_{total_count}"

    LAST_MARKET_DATA_HEALTH = {
        "sane_for_entries": sane_for_entries,
        "reject_count": reject_count,
        "large_tick_count": large_tick_count,
        "trusted_count": trusted_count,
        "total_count": total_count,
        "reason": reason,
    }

    if not sane_for_entries:
        print("PRICE VALIDATION ENTRY BLOCK:", LAST_MARKET_DATA_HEALTH)

    return clean

def total_exposure(prices):
    return sum((qty * prices.get(symbol, 0.0) for symbol, qty in portfolio.positions.items()))

def symbol_exposure(symbol, prices):
    return portfolio.positions.get(symbol, 0.0) * prices.get(symbol, 0.0)

def cleanup_expired_quarantines():
    try:
        with engine.begin() as conn:
            conn.execute(text("\n                DELETE FROM symbol_quarantine\n                WHERE blocked_until IS NOT NULL\n                  AND blocked_until <= DATETIME('now', '+3 hours')\n            "))
    except Exception:
        pass
LAST_GLOBAL_BUY_TS = 0.0
LAST_BUY_BY_SYMBOL_TS = {}

def _now_ts():
    import time
    return time.time()

def _entry_pacing_seconds(regime: str) -> int:
    r = str(regime or '').upper()
    if r == 'RISK_OFF':
        return int(float(getattr(settings, 'risk_off_min_seconds_between_buys', 1200)))
    if r == 'SIDEWAYS':
        return int(float(getattr(settings, 'sideways_min_seconds_between_buys', 900)))
    return int(float(getattr(settings, 'trend_min_seconds_between_buys', 600)))

def _same_symbol_pacing_seconds(regime: str) -> int:
    r = str(regime or '').upper()
    if r == 'RISK_OFF':
        return int(float(getattr(settings, 'risk_off_same_symbol_cooldown_seconds', 3600)))
    if r == 'SIDEWAYS':
        return int(float(getattr(settings, 'sideways_same_symbol_cooldown_seconds', 2400)))
    return int(float(getattr(settings, 'trend_same_symbol_cooldown_seconds', 1800)))

def _strong_symbol_trend_for_risk_off(fill_or_feature: dict) -> bool:
    try:
        f = fill_or_feature or {}
        if isinstance(f.get('feature'), dict):
            f = f['feature']
        elif isinstance(f.get('features'), dict):
            f = f['features']
        source = str(f.get('source', 'NORMAL')).upper()
        if source == 'RAW_MOMENTUM_FALLBACK':
            return False
        symbol_regime = str(f.get('symbol_regime', '')).upper()
        trend = float(f.get('trend', 0.0) or 0.0)
        momentum = float(f.get('momentum', 0.0) or 0.0)
        one_tick = float(f.get('one_tick_momentum', 0.0) or 0.0)
        signal = float(f.get('signal_strength', f.get('confidence', 0.0)) or 0.0)
        quality = float(f.get('trend_quality', 0.0) or 0.0)
        if symbol_regime not in ('SYMBOL_TREND_UP', 'SYMBOL_BREAKOUT_UP'):
            return False
        if trend <= 0 or momentum <= 0 or one_tick < -0.0015:
            return False
        return signal >= 0.004 or quality >= 0.004 or (trend > 0.002 and momentum > 0.002)
    except Exception:
        return False
SYMBOL_BUY_COUNTS = {}
SYMBOL_EXCEPTIONAL_LADDER = [0.045, 0.05, 0.055, 0.06, 0.065, 0.07, 0.075]

def _fill_signal_strength(fill: dict) -> float:
    try:
        if not isinstance(fill, dict):
            return 0.0
        for key in ('signal_strength', 'confidence', 'score'):
            if key in fill:
                return float(fill.get(key) or 0.0)
        f = fill.get('feature') or fill.get('features') or {}
        if isinstance(f, dict):
            for key in ('signal_strength', 'confidence', 'trend_quality', 'breakout_score'):
                if key in f:
                    return float(f.get(key) or 0.0)
        return 0.0
    except Exception:
        return 0.0

def symbol_exclusion_ladder_allows(symbol: str, fill: dict) -> tuple[bool, str]:
    count = int(SYMBOL_BUY_COUNTS.get(symbol, 0) or 0)
    if count < 3:
        return (True, f'normal_symbol_buy_count_{count}')
    ladder_index = min(count - 3, len(SYMBOL_EXCEPTIONAL_LADDER) - 1)
    required = float(SYMBOL_EXCEPTIONAL_LADDER[ladder_index])
    signal = _fill_signal_strength(fill)
    if signal >= required:
        return (True, f'exceptional_symbol_ladder_pass count={count} signal={signal:.5f} required={required:.5f}')
    return (False, f'symbol_excluded_after_3_buys count={count} signal={signal:.5f} required={required:.5f}')

def symbol_exclusion_ladder_mark_buy(symbol: str):
    SYMBOL_BUY_COUNTS[symbol] = int(SYMBOL_BUY_COUNTS.get(symbol, 0) or 0) + 1

def final_entry_pacing_allows(symbol: str, fill: dict, regime: str) -> tuple[bool, str]:
    global LAST_GLOBAL_BUY_TS, LAST_BUY_BY_SYMBOL_TS
    now = _now_ts()
    global_gap = _entry_pacing_seconds(regime)
    symbol_gap = _same_symbol_pacing_seconds(regime)
    since_global = now - float(LAST_GLOBAL_BUY_TS or 0.0)
    if LAST_GLOBAL_BUY_TS and since_global < global_gap:
        return (False, f'global_buy_pacing_wait {int(global_gap - since_global)}s')
    last_sym = float(LAST_BUY_BY_SYMBOL_TS.get(symbol, 0.0) or 0.0)
    since_sym = now - last_sym
    if last_sym and since_sym < symbol_gap:
        return (False, f'same_symbol_buy_pacing_wait {int(symbol_gap - since_sym)}s')
    if str(regime or '').upper() == 'RISK_OFF':
        if not _strong_symbol_trend_for_risk_off(fill):
            return (False, 'risk_off_requires_strong_symbol_trend')
    ladder_ok, ladder_reason = symbol_exclusion_ladder_allows(symbol, fill)
    if not ladder_ok:
        return (False, ladder_reason)
    return (True, 'allowed')

def final_entry_pacing_mark_buy(symbol: str):
    global LAST_GLOBAL_BUY_TS, LAST_BUY_BY_SYMBOL_TS
    now = _now_ts()
    LAST_GLOBAL_BUY_TS = now
    LAST_BUY_BY_SYMBOL_TS[symbol] = now

def can_buy(symbol, fill, prices, equity):
    cleanup_expired_quarantines()
    if not ALLOW_BUYS:
        return (False, 'buys_disabled')
    if symbol in EXCLUDED_TRADING_SYMBOLS:
        return (False, 'excluded_quote_or_stable_symbol')

    try:
        fill_price_for_gate = float(fill.get('fill_price') or fill.get('expected_price') or 0.0)
        if fill_price_for_gate < 0.001:
            return (False, f'price_too_low_for_entry_{fill_price_for_gate}')
    except Exception:
        return (False, 'invalid_fill_price_for_entry')
    try:
        open_positions_count = sum((1 for _, q in portfolio.positions.items() if float(q or 0) > 1e-08))
        current_total_exposure = total_exposure(prices)
        current_exposure_pct = current_total_exposure / max(float(equity or 0), 1e-06)
        if str(last_known_regime or '').upper() == 'SIDEWAYS':
            if open_positions_count >= 8:
                return (False, f'sideways_max_open_positions_{open_positions_count}')
            if current_exposure_pct >= 0.15:
                return (False, f'sideways_max_exposure_{current_exposure_pct:.4f}')
    except Exception:
        pass
    try:
        current_drawdown = float(getattr(portfolio, 'drawdown', 0.0) or 0.0)
        caution_drawdown = float(getattr(settings, 'caution_drawdown', -0.02))
        blocked_drawdown = float(getattr(settings, 'blocked_drawdown', -0.05))
        if current_drawdown <= blocked_drawdown * 0.9:
            return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')
        if current_drawdown <= caution_drawdown:
            open_positions_count = sum((1 for _, q in portfolio.positions.items() if float(q or 0) > 1e-08))
            try:
                current_exposure = float(getattr(portfolio, 'exposure', 0.0) or 0.0)
                current_exposure_pct = current_exposure / max(float(equity or 0.0), 1e-06)
            except Exception:
                current_exposure_pct = 0.0
            if current_exposure_pct >= 0.2:
                return (False, f'caution_mode_exposure_{current_exposure_pct:.4f}')
            if open_positions_count >= 10:
                return (False, f'caution_mode_max_positions_{open_positions_count}')
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT\n                    SUM(CASE WHEN strategy='stop_loss' THEN 1 ELSE 0 END) AS stop_losses,\n                    SUM(CASE WHEN strategy='take_profit' THEN 1 ELSE 0 END) AS take_profits\n                FROM trades\n                WHERE symbol = :symbol\n                  AND side = 'sell'\n                  AND created_at >= DATETIME('now', '+3 hours', '-3 hours')\n            "), {'symbol': symbol}).mappings().first()
            if row:
                stop_losses = int(row['stop_losses'] or 0)
                take_profits = int(row['take_profits'] or 0)
                if stop_losses >= 3 and stop_losses >= take_profits + 2:
                    return (False, f'symbol_bad_history_sl{stop_losses}_tp{take_profits}')
    except Exception:
        pass
    if portfolio.positions.get(symbol, 0.0) > 1e-08:
        return (False, 'already_holding_symbol')
    if symbol in quarantined_symbols and time.time() < quarantined_symbols[symbol]:
        return (False, 'symbol_quarantined')
    if equity <= INITIAL_EQUITY * (1 - DAILY_LOSS_LIMIT_PCT):
        return (False, 'daily_loss_limit')
    now = time.time()
    if now - last_trade_time.get(symbol, 0) < COOLDOWN_SECONDS:
        return (False, 'cooldown')
    recent_symbol_trades = recent_symbol_buy_count(symbol)
    trade_counts[symbol] = recent_symbol_trades
    if recent_symbol_trades >= MAX_TRADES_PER_SYMBOL:
        return (False, f'max_trades_per_symbol_recent_{recent_symbol_trades}_in_{TRADE_COUNT_WINDOW_HOURS:g}h')
    fill_value = float(fill['quantity']) * float(fill['fill_price'])
    current_total_exposure = total_exposure(prices)
    if current_total_exposure + fill_value > equity * MAX_TOTAL_EXPOSURE_PCT:
        return (False, 'max_total_exposure')
    current_symbol_exposure = symbol_exposure(symbol, prices)
    if current_symbol_exposure + fill_value > equity * MAX_SYMBOL_EXPOSURE_PCT:
        return (False, 'max_symbol_exposure')
    return (True, 'approved')

def apply_buy(fill):
    symbol = fill['symbol']
    qty = float(fill['quantity'])
    price = float(fill['fill_price'])
    fee = qty * price * FEE_RATE
    cost = qty * price + fee
    if portfolio.cash < cost:
        return False
    old_qty = portfolio.positions.get(symbol, 0.0)
    old_avg = entry_prices.get(symbol, price)
    new_qty = old_qty + qty
    new_avg = (old_qty * old_avg + qty * price) / new_qty
    portfolio.cash -= cost
    portfolio.positions[symbol] = new_qty
    entry_prices[symbol] = new_avg
    trade_counts[symbol] = trade_counts.get(symbol, 0) + 1
    last_trade_time[symbol] = time.time()
    return True

def apply_sell(symbol, qty, price, reason):
    held = portfolio.positions.get(symbol, 0.0)
    avg_entry = entry_prices.get(symbol)
    if avg_entry:
        try:
            px = float(price)
            avg = float(avg_entry)
            deviation = abs(px - avg) / avg if avg > 0 else 0.0
            if deviation > 0.2:
                print(f'EXIT PRICE HARD BLOCK {symbol}: reason={reason} price={px} avg_entry={avg} deviation={deviation:.2%}')
                return None
        except Exception as e:
            print(f'EXIT PRICE HARD BLOCK ERROR {symbol}: {e}')
            return None
    sell_qty = min(qty, held)
    if sell_qty <= 0:
        return None
    fee = sell_qty * price * FEE_RATE
    portfolio.cash += sell_qty * price - fee
    portfolio.positions[symbol] = held - sell_qty
    if portfolio.positions[symbol] <= 1e-08:
        portfolio.positions[symbol] = 0.0
        entry_prices.pop(symbol, None)
        trade_counts[symbol] = 0
    return {'symbol': symbol, 'side': 'sell', 'quantity': sell_qty, 'expected_price': price, 'fill_price': price, 'slippage_bps': 0, 'strategy': reason, 'confidence': 1.0}

def apply_shadow_buy(fill):
    symbol = fill['symbol']
    qty = float(fill['quantity'])
    price = float(fill['fill_price'])
    strategy = fill.get('strategy', 'unknown')
    old_qty = shadow_positions.get(symbol, 0.0)
    old_avg = shadow_entry_prices.get(symbol, price)
    new_qty = old_qty + qty
    new_avg = (old_qty * old_avg + qty * price) / new_qty
    shadow_positions[symbol] = new_qty
    shadow_entry_prices[symbol] = new_avg
    shadow_trade_counts[symbol] = shadow_trade_counts.get(symbol, 0) + 1
    return True

def apply_shadow_sell(symbol, qty, price, reason):
    held = shadow_positions.get(symbol, 0.0)
    avg_entry = shadow_entry_prices.get(symbol)
    if avg_entry:
        try:
            px = float(price)
            avg = float(avg_entry)
            deviation = abs(px - avg) / avg if avg > 0 else 0.0
            if deviation > 0.2:
                print(f'SHADOW EXIT PRICE HARD BLOCK {symbol}: reason={reason} price={px} avg_entry={avg} deviation={deviation:.2%}')
                return None
        except Exception as e:
            print(f'SHADOW EXIT PRICE HARD BLOCK ERROR {symbol}: {e}')
            return None
    sell_qty = min(qty, held)
    if sell_qty <= 0:
        return None
    shadow_positions[symbol] = held - sell_qty
    if shadow_positions[symbol] <= 1e-08:
        shadow_positions[symbol] = 0.0
        shadow_entry_prices.pop(symbol, None)
    return {'symbol': symbol, 'side': 'sell', 'quantity': sell_qty, 'expected_price': price, 'fill_price': price, 'slippage_bps': 0, 'strategy': reason, 'confidence': 1.0, 'shadow_mode': True}

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
        history = globals().get('PRICE_HISTORY', {}).get(symbol, [])
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
    r = str(regime or '').upper()
    base_stop = float(STOP_LOSS_PCT)
    base_take = float(TAKE_PROFIT_PCT)
    if r == 'SIDEWAYS':
        stop = max(base_stop, vol * 2.5, 0.012)
        take = max(base_take, stop * 1.8, 0.02)
    elif r == 'RISK_OFF':
        stop = max(base_stop * 0.75, vol * 1.8, 0.008)
        take = max(base_take, stop * 1.5)
    else:
        stop = max(base_stop, vol * 2.2, 0.01)
        take = max(base_take, stop * 2.0, 0.022)
    stop = min(stop, 0.035)
    take = min(take, 0.08)
    return (stop, take)

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
    position_open_time.pop('shadow_' + symbol, None)
    position_peak_change.pop('shadow_' + symbol, None)

def valid_exit_price(symbol, price, avg_entry, reason='exit'):
    """
    Prevent corrupted market ticks from creating fake paper PnL.
    A normal crypto exit should not be hundreds/thousands of percent away
    from the recorded entry price in one cycle.
    """
    try:
        p = float(price or 0)
        a = float(avg_entry or 0)
        if p <= 0 or a <= 0:
            print(f'EXIT PRICE BLOCK {symbol}: invalid price={p} avg_entry={a} reason={reason}')
            return False
        ratio = p / a
        if ratio > 3.0 or ratio < 0.333:
            print(f'EXIT PRICE BLOCK {symbol}: suspicious_exit_price price={p:.8f} avg_entry={a:.8f} ratio={ratio:.4f} reason={reason}')
            return False
        return True
    except Exception as e:
        print(f'EXIT PRICE BLOCK {symbol}: validation_error {e}')
        return False


def _qfos_position_age_minutes(symbol):
    try:
        opened = position_open_time.get(symbol)
        if opened is None:
            return 0.0
        if isinstance(opened, (int, float)):
            return max(0.0, (time.time() - float(opened)) / 60.0)
        if isinstance(opened, datetime):
            return max(0.0, (datetime.utcnow() - opened.replace(tzinfo=None)).total_seconds() / 60.0)
    except Exception:
        pass
    return 0.0

def _qfos_record_peak_change(symbol, change):
    try:
        prev = float(position_peak_change.get(symbol, change) or change)
        peak = max(prev, float(change))
        position_peak_change[symbol] = peak
        return peak
    except Exception:
        return float(change or 0.0)

def _qfos_take_profit_target(regime):
    try:
        if str(regime or "").upper() == "SIDEWAYS":
            return min(float(FULL_TAKE_PROFIT_PCT), float(WIN_RATE_SIDEWAYS_FULL_TP_PCT))
        return float(FULL_TAKE_PROFIT_PCT)
    except Exception:
        return 0.008

def _qfos_exit_decision(symbol, change, peak_change, age_minutes, regime):
    """
    Returns an exit reason or None.

    This is designed to improve win rate safely:
    - hard stop remains first priority
    - quick full TP in sideways
    - breakeven protection after a favorable move
    - trailing profit after a stronger favorable move
    - time stop before stale trades decay into stop loss
    """
    dynamic_stop_pct, dynamic_take_pct = adaptive_exit_thresholds(symbol, regime)
    tp_target = min(float(dynamic_take_pct), _qfos_take_profit_target(regime))

    # 1. Hard stop: keep capital protection.
    if change <= -float(dynamic_stop_pct):
        return "adaptive_stop_loss"

    # 2. Full TP earlier, especially in SIDEWAYS.
    if change >= tp_target:
        return "adaptive_take_profit"

    # 3. Trailing profit lock: if trade was nicely positive and gives back gains, exit while still green.
    if peak_change >= WIN_RATE_TRAIL_TRIGGER_PCT:
        if change >= BREAKEVEN_EXIT_PCT and change <= (peak_change - WIN_RATE_TRAIL_GIVEBACK_PCT):
            return "trailing_profit_exit"

    # 4. Breakeven protection: once it has been sufficiently positive, do not let it become a loser.
    if age_minutes >= WIN_RATE_MIN_HOLD_BEFORE_BREAKEVEN_MIN:
        if peak_change >= BREAKEVEN_TRIGGER_PCT and change <= BREAKEVEN_EXIT_PCT:
            return "breakeven_protection_exit"

    # 5. Time stop: stale SIDEWAYS trades should not be allowed to drift into stop loss.
    if age_minutes >= TIME_STOP_MINUTES and change <= TIME_STOP_EXIT_BELOW_PCT:
        return "time_stop_exit"

    return None


def generate_sells(prices, regime):
    sells = []
    if not ALLOW_SELLS:
        return sells

    # Real positions.
    for symbol, qty in list(portfolio.positions.items()):
        if qty <= 0:
            continue

        price = prices.get(symbol)
        avg_entry = entry_prices.get(symbol)

        if not price or not avg_entry:
            continue

        if not valid_exit_price(symbol, price, avg_entry, 'position_exit'):
            continue

        try:
            change = (float(price) - float(avg_entry)) / float(avg_entry)
        except Exception:
            continue

        peak_change = _qfos_record_peak_change(symbol, change)
        age_minutes = _qfos_position_age_minutes(symbol)

        reason = _qfos_exit_decision(symbol, change, peak_change, age_minutes, regime)

        if reason:
            # trailing_profit_exit is a true winning exit, but apply_sell can store any reason.
            sell = normalize_exit_order(apply_sell(symbol, qty, price, reason))
            if sell:
                print(
                    f"[WIN_RATE_EXIT] {reason} {symbol} "
                    f"change={change:.4f} peak={peak_change:.4f} age_min={age_minutes:.1f}"
                )
                sells.append(sell)
            continue

        # Risk-off remains guarded by the winning-strategy logic.
        if regime == 'RISK_OFF':
            risk_position = {
                'symbol': symbol,
                'quantity': qty,
                'entry_price': avg_entry,
                'mark_price': price,
                'price': price,
                'side': 'long',
            }
            if allow_risk_off_exit(risk_position, {'regime': regime}, portfolio, reason='risk_off_exit'):
                sell = normalize_exit_order(apply_sell(symbol, qty, price, 'risk_off_exit'))
                if sell:
                    sells.append(sell)
            else:
                print(f"[WINNING_STRATEGY] Suppressed false risk_off_exit for {symbol} at price={price}")

    # Shadow positions.
    for symbol, qty in list(shadow_positions.items()):
        if qty <= 0:
            continue

        price = prices.get(symbol)
        avg_entry = shadow_entry_prices.get(symbol)

        if not price or not avg_entry:
            continue

        if not valid_exit_price(symbol, price, avg_entry, 'shadow_position_exit'):
            continue

        try:
            change = (float(price) - float(avg_entry)) / float(avg_entry)
        except Exception:
            continue

        # Use separate shadow peak key to avoid mixing real and shadow state.
        shadow_peak_key = f"SHADOW::{symbol}"
        peak_change = _qfos_record_peak_change(shadow_peak_key, change)
        age_minutes = _qfos_position_age_minutes(symbol)

        reason = _qfos_exit_decision(symbol, change, peak_change, age_minutes, regime)

        if reason:
            sell = normalize_exit_order(apply_shadow_sell(symbol, qty, price, reason))
            if sell:
                print(
                    f"[WIN_RATE_EXIT][SHADOW] {reason} {symbol} "
                    f"change={change:.4f} peak={peak_change:.4f} age_min={age_minutes:.1f}"
                )
                sells.append(sell)
            continue

        if regime == 'RISK_OFF':
            risk_position = {
                'symbol': symbol,
                'quantity': qty,
                'entry_price': avg_entry,
                'mark_price': price,
                'price': price,
                'side': 'long',
            }
            if allow_risk_off_exit(risk_position, {'regime': regime}, portfolio, reason='risk_off_exit'):
                sell = normalize_exit_order(apply_shadow_sell(symbol, qty, price, 'risk_off_exit'))
                if sell:
                    sells.append(sell)
            else:
                print(f"[WINNING_STRATEGY] Suppressed false shadow risk_off_exit for {symbol} at price={price}")

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
        sell = apply_sell(symbol, qty_to_sell, price, 'emergency_exposure_reduction')
        if sell:
            sells.append(sell)
        excess -= value_to_sell
    return sells

def save_trade(conn, fill):
    conn.execute(text("\n        INSERT INTO trades(\n            symbol, side, quantity, expected_price, fill_price,\n            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at\n        )\n        VALUES(\n            :symbol, :side, :quantity, :expected_price, :fill_price,\n            :slippage_bps, 0, :strategy, :confidence, :live, :shadow_mode, DATETIME('now', '+3 hours')\n        )\n    "), fill | {'live': settings.live_trading, 'shadow_mode': fill.get('shadow_mode', False)})

def ensure_positions_table():
    with engine.begin() as conn:
        conn.execute(text("\n            CREATE TABLE IF NOT EXISTS positions (\n                symbol TEXT PRIMARY KEY,\n                quantity REAL NOT NULL DEFAULT 0,\n                avg_entry REAL NOT NULL DEFAULT 0,\n                realized_pnl REAL NOT NULL DEFAULT 0,\n                unrealized_pnl REAL NOT NULL DEFAULT 0,\n                last_price REAL NOT NULL DEFAULT 0,\n                exposure REAL NOT NULL DEFAULT 0,\n                strategy TEXT,\n                updated_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))\n            )\n        "))
        cols = [r[1] for r in conn.execute(text('PRAGMA table_info(positions)'))]
        if 'strategy' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN strategy TEXT'))
        conn.execute(text("\n            CREATE TABLE IF NOT EXISTS symbol_quarantine (\n                symbol TEXT PRIMARY KEY,\n                reason TEXT NOT NULL,\n                blocked_until DATETIME,\n                created_at DATETIME DEFAULT (DATETIME('now', '+3 hours'))\n            )\n        "))

def quarantine_symbol(symbol: str, reason: str, hours: int=24):
    with engine.begin() as conn:
        conn.execute(text("\n            INSERT INTO symbol_quarantine (symbol, reason, blocked_until, created_at)\n            VALUES (:symbol, :reason, datetime('now', '+' || :hours || ' hours', '+3 hours'), datetime('now', '+3 hours'))\n            ON CONFLICT (symbol) DO UPDATE SET\n            reason = EXCLUDED.reason,\n            blocked_until = EXCLUDED.blocked_until,\n            created_at = EXCLUDED.created_at\n        "), {'symbol': symbol, 'reason': reason, 'hours': hours})
    send_telegram_alert(f'<b>Symbol Quarantined</b>\nSymbol: {symbol}\nReason: {reason}')

def update_position_from_fill(conn, fill):
    symbol = fill['symbol']
    side = fill['side']
    qty = float(fill['quantity'])
    price = float(fill['fill_price'])
    existing = conn.execute(text('\n        SELECT symbol, quantity, avg_entry, realized_pnl, strategy\n        FROM positions\n        WHERE symbol = :symbol\n    '), {'symbol': symbol}).mappings().first()
    if not existing:
        existing_qty = 0.0
        avg_entry = 0.0
        realized_pnl = 0.0
    else:
        existing_qty = float(existing['quantity'] or 0)
        avg_entry = float(existing['avg_entry'] or 0)
        realized_pnl = float(existing['realized_pnl'] or 0)
        existing_strategy = existing['strategy'] or 'unknown'
    if side == 'buy':
        new_qty = existing_qty + qty
        new_strategy = fill.get('strategy', existing_strategy if existing else 'unknown')
        fee_adjusted_price = price * (1 + FEE_RATE)
        if new_qty > 0:
            new_avg_entry = (existing_qty * avg_entry + qty * fee_adjusted_price) / new_qty
        else:
            new_avg_entry = 0.0
        new_realized_pnl = realized_pnl
        fill_pnl = 0.0
        applied_strategy = new_strategy
    elif side == 'sell':
        sell_qty = min(qty, existing_qty)
        net_sell_price = price * (1 - FEE_RATE)
        fill_pnl = sell_qty * (net_sell_price - avg_entry)
        new_qty = max(existing_qty - sell_qty, 0.0)
        new_avg_entry = avg_entry if new_qty > 0 else 0.0
        new_realized_pnl = realized_pnl + fill_pnl
        new_strategy = existing_strategy
        applied_strategy = existing_strategy
        if new_realized_pnl <= -2.0:
            quarantine_symbol(symbol, f'realized_pnl_exceeded_limit_{new_realized_pnl:.2f}')
    else:
        return 0.0
    exposure = new_qty * price
    unrealized_pnl = new_qty * (price - new_avg_entry) if new_qty > 0 else 0.0
    conn.execute(text("\n        INSERT INTO positions(\n            symbol, quantity, avg_entry, realized_pnl,\n            unrealized_pnl, last_price, exposure, strategy, updated_at\n        )\n        VALUES(\n            :symbol, :quantity, :avg_entry, :realized_pnl,\n            :unrealized_pnl, :last_price, :exposure, :strategy, DATETIME('now', '+3 hours')\n        )\n        ON CONFLICT (symbol)\n        DO UPDATE SET\n            quantity = EXCLUDED.quantity,\n            avg_entry = EXCLUDED.avg_entry,\n            realized_pnl = EXCLUDED.realized_pnl,\n            unrealized_pnl = EXCLUDED.unrealized_pnl,\n            last_price = EXCLUDED.last_price,\n            exposure = EXCLUDED.exposure,\n            strategy = EXCLUDED.strategy,\n            updated_at = DATETIME('now', '+3 hours')\n    "), {'symbol': symbol, 'quantity': new_qty, 'avg_entry': new_avg_entry, 'realized_pnl': new_realized_pnl, 'unrealized_pnl': unrealized_pnl, 'last_price': price, 'exposure': exposure, 'strategy': new_strategy})
    return (fill_pnl, applied_strategy)

def mark_positions_to_market(conn, prices):
    for symbol, price in prices.items():
        row = conn.execute(text('\n            SELECT quantity, avg_entry, realized_pnl\n            FROM positions\n            WHERE symbol = :symbol\n        '), {'symbol': symbol}).mappings().first()
        if not row:
            continue
        qty = float(row['quantity'] or 0)
        avg_entry = float(row['avg_entry'] or 0)
        exposure = qty * float(price)
        unrealized_pnl = qty * (float(price) - avg_entry) if qty > 0 else 0.0
        conn.execute(text("\n            UPDATE positions\n            SET last_price = :last_price,\n                exposure = :exposure,\n                unrealized_pnl = :unrealized_pnl,\n                updated_at = DATETIME('now', '+3 hours')\n            WHERE symbol = :symbol\n        "), {'symbol': symbol, 'last_price': float(price), 'exposure': exposure, 'unrealized_pnl': unrealized_pnl})
wait_for_database()
ensure_positions_table()

def send_auto_pause(reason: str, equity: float, exposure: float, regime: str):
    global last_auto_pause_reason
    if last_auto_pause_reason == reason:
        return
    last_auto_pause_reason = reason
    pause_bot(reason)
    send_telegram_alert(f'<b>Quant Fund OS AUTO-PAUSED</b>\nReason: {reason}\nEquity: {equity:.2f}\nExposure: {exposure:.2f}\nRegime: {regime}\nLive trading: {settings.live_trading}')
    try:
        send_telegram_alert(msg)
    except Exception as e:
        print('Auto-pause Telegram alert failed:', e)

def get_day_start_equity(default_equity: float=100.0):
    with engine.begin() as conn:
        row = conn.execute(text("\n            SELECT equity\n            FROM portfolio_snapshots\n            WHERE created_at >= date('now')\n            ORDER BY id ASC\n            LIMIT 1\n        ")).mappings().first()
    if not row:
        return default_equity
    return float(row['equity'] or default_equity)

def check_daily_loss_guard(equity: float, exposure: float, regime: str):
    day_start_equity = get_day_start_equity(INITIAL_EQUITY)
    if day_start_equity <= 0:
        return False
    daily_pnl_pct = (equity - day_start_equity) / day_start_equity
    if daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
        send_auto_pause(f'max_daily_loss_hit_{daily_pnl_pct:.2%}', equity, exposure, regime)
        return True
    return False

def recent_buy_count():
    with engine.begin() as conn:
        row = conn.execute(text("\n            SELECT COUNT(*) AS count\n            FROM trades\n            WHERE side = 'buy'\n              AND created_at >= datetime('now', '+3 hours', '-1 hour')\n        ")).mappings().first()
    return int(row['count'] or 0) if row else 0

def recent_symbol_buy_count(symbol: str):
    """
    Count recent buys for a symbol in a rolling window.
    This replaces lifetime trade_counts blocking.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT COUNT(*) AS count\n                FROM trades\n                WHERE symbol = :symbol\n                  AND side = 'buy'\n                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')\n            "), {'symbol': symbol, 'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().first()
        return int(row['count'] or 0) if row else 0
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
            rows = conn.execute(text("\n                SELECT symbol, COUNT(*) AS count\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')\n                GROUP BY symbol\n            "), {'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().all()
        for r in rows:
            trade_counts[r['symbol']] = int(r['count'] or 0)
    except Exception as e:
        print('TRADE_COUNT_REFRESH_ERROR:', e)

def is_trending_regime(regime: str):
    r = str(regime or '').upper()
    return r in {'BULL', 'BULLISH', 'TRENDING', 'UPTREND', 'TREND'}

def entry_policy_allows(symbol: str, regime: str, confidence: float, entries_this_cycle: int, strategy: str=None):
    with engine.begin() as conn:
        q = conn.execute(text("SELECT symbol FROM symbol_quarantine WHERE symbol = :sym AND blocked_until IS NOT NULL AND blocked_until > DATETIME('now', '+3 hours')"), {'sym': symbol}).first()
        if q:
            return (False, 'symbol_quarantined')
        if strategy and strategy != 'fallback_scout_breakout':
            s_score = conn.execute(text('SELECT status FROM strategy_scores WHERE strategy = :s'), {'s': strategy}).mappings().first()
            if s_score and s_score['status'] == 'blocked':
                return (False, f'strategy_{strategy}_blocked')
    r = str(regime or '').upper()
    if r == 'RISK_OFF':
        if strategy == 'fallback_scout_breakout':
            if confidence >= QFOS_SCOUT_CONFIDENCE:
                return (True, 'risk_off_scout_fallback_allowed')
            return (False, f'risk_off_scout_confidence_too_low_{confidence:.2f}')
        return (False, 'risk_off_blocks_new_buys')
    recent_entries = recent_buy_count() + entries_this_cycle
    if r == 'SIDEWAYS':
        if confidence < SIDEWAYS_MIN_CONFIDENCE:
            return (False, f'sideways_confidence_too_low_{confidence:.2f}')
        if recent_entries >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
            return (False, 'sideways_max_entries_per_hour_hit')
        return (True, 'ok')
    if is_trending_regime(r):
        if confidence < TRENDING_MIN_CONFIDENCE:
            return (False, f'trending_confidence_too_low_{confidence:.2f}')
        if recent_entries >= TRENDING_MAX_ENTRIES_PER_HOUR:
            return (False, 'trending_max_entries_per_hour_hit')
        return (True, 'ok')
    if confidence < SIDEWAYS_MIN_CONFIDENCE:
        return (False, f'default_confidence_too_low_{confidence:.2f}')
    if recent_entries >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
        return (False, 'default_max_entries_per_hour_hit')
    return (True, 'ok')

def reset_liquidity_errors():
    global liquidity_error_times
    liquidity_error_times = []

def register_liquidity_error(error_message: str):
    global liquidity_error_times
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LIQUIDITY_ERROR_WINDOW_SECONDS)
    liquidity_error_times = [t for t in liquidity_error_times if t >= cutoff]
    liquidity_error_times.append(now)
    if len(liquidity_error_times) >= LIQUIDITY_ERROR_LIMIT:
        send_auto_pause('liquidity_error_circuit_breaker', 0.0, 0.0, 'UNKNOWN')

def final_trade_firewall(fill, regime):
    side = str(fill.get('side', '')).lower()
    strategy = str(fill.get('strategy', ''))
    confidence = float(fill.get('confidence', 0))
    if side == 'sell':
        return (True, 'sell_allowed')
    if side != 'buy':
        return (False, 'unknown_side_blocked')
    r = str(regime or '').upper()
    if r == 'RISK_OFF':
        if strategy == 'fallback_scout_breakout' and confidence >= QFOS_SCOUT_CONFIDENCE and _strong_symbol_trend_for_risk_off(fill):
            return (True, 'risk_off_scout_fallback_allowed')
        return (False, 'risk_off_blocks_buy')
    if r == 'SIDEWAYS' and confidence < SIDEWAYS_MIN_CONFIDENCE:
        return (False, f'sideways_confidence_too_low_{confidence:.2f}')
    if is_trending_regime(r) and confidence < TRENDING_MIN_CONFIDENCE:
        return (False, f'trending_confidence_too_low_{confidence:.2f}')
    if r not in {'SIDEWAYS', 'BULL', 'BULLISH', 'TRENDING', 'UPTREND', 'TREND'}:
        return (False, f'unknown_regime_blocks_buy_{r}')
    return (True, 'buy_allowed')
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
            if abs_momentum < 0.0015:
                continue
            direction = 'BUY' if momentum > 0 else 'SELL'
            signal_strength = min(1.0, abs_momentum * 80 + volatility * 20)
            fallback_features[symbol] = {'ready': True, 'source': 'RAW_MOMENTUM_FALLBACK', 'direction': direction, 'price': current_price, 'trend': momentum, 'long_trend': momentum, 'momentum': momentum, 'one_tick_momentum': momentum, 'volatility': volatility, 'signal_strength': signal_strength, 'confidence': signal_strength, 'reason': 'normal_features_empty'}
        except Exception:
            continue
    ranked = sorted(fallback_features.items(), key=lambda item: item[1].get('signal_strength', 0), reverse=True)
    return dict(ranked[:5])

def build_fallback_orders(fallback_features, prices, equity, cash, regime, max_orders=2):
    orders = []
    if not isinstance(fallback_features, dict):
        return orders
    ranked = sorted(fallback_features.items(), key=lambda item: item[1].get('signal_strength', 0), reverse=True)
    for symbol, data in ranked:
        if len(orders) >= max_orders:
            break
        if not isinstance(data, dict):
            continue
        if data.get('direction') != 'BUY':
            continue
        price = float(prices.get(symbol, 0) or data.get('price', 0) or 0)
        if price <= 0:
            continue
        confidence = float(data.get('confidence', data.get('signal_strength', 0)) or 0)
        r = str(regime or '').upper()
        if r == 'SIDEWAYS':
            confidence = max(confidence, float(SIDEWAYS_MIN_CONFIDENCE) + 0.01)
        elif is_trending_regime(r):
            confidence = max(confidence, float(TRENDING_MIN_CONFIDENCE) + 0.01)
        confidence = min(confidence, 0.95)
        position_value = min(max(float(equity or 0) * 0.025, 1.0), float(cash or 0) * 0.05)
        if position_value <= 0:
            continue
        qty = position_value / price
        orders.append({'symbol': symbol, 'side': 'buy', 'quantity': qty, 'expected_price': price, 'fill_price': price, 'slippage_bps': 0, 'strategy': 'raw_momentum_fallback', 'confidence': confidence})
    return orders

def log_cycle_diagnostic(market_data=None, features=None, orders=None, portfolio=None, rejected=None, note=''):

    # QFOS_SAFE_DIAGNOSTIC_PORTFOLIO_DICT_FIX
    # log_cycle_diagnostic may receive portfolio as a dict payload.
    # Convert it to an object-like wrapper so existing diagnostic code can use .positions/.cash safely.
    if isinstance(portfolio, dict):
        class _QFOSDiagnosticPortfolio:
            pass

        _qfos_p = _QFOSDiagnosticPortfolio()
        _qfos_p.positions = portfolio.get("positions", {}) or {}
        _qfos_p.cash = float(portfolio.get("cash", 0) or 0)
        _qfos_p.equity = float(portfolio.get("equity", 0) or 0)
        _qfos_p.drawdown = float(portfolio.get("drawdown", 0) or 0)
        portfolio = _qfos_p
    global EMPTY_FEATURE_CYCLES
    market_count = len(market_data) if isinstance(market_data, dict) else 0
    feature_count = len(features) if isinstance(features, dict) else 0
    order_count = len(orders) if isinstance(orders, list) else 0
    if feature_count == 0:
        EMPTY_FEATURE_CYCLES += 1
    else:
        EMPTY_FEATURE_CYCLES = 0
    if market_count == 0:
        no_trade_reason = 'no_market_data'
    elif feature_count == 0:
        no_trade_reason = 'features_empty'
    elif order_count == 0:
        no_trade_reason = 'features_exist_but_no_orders'
    else:
        no_trade_reason = 'orders_created_or_applied'
    print('\n' + '=' * 72)
    print('QUANT FUND OS — CYCLE DIAGNOSTIC')
    print(f'Time: {datetime.utcnow().isoformat()}Z')
    print(f'Market symbols: {market_count}')
    print(f'Feature symbols: {feature_count}')
    print(f'Orders/applied fills: {order_count}')
    print(f'Empty feature cycles: {EMPTY_FEATURE_CYCLES}')
    print(f'No-trade reason: {no_trade_reason}')
    if isinstance(portfolio, dict):
        print(f"Regime: {portfolio.get('regime')}")
        print(f"Risk: {portfolio.get('risk_status')}")
        print(f"Equity: {portfolio.get('equity')}")
        print(f"Cash: {portfolio.get('cash')}")
        print(f"Exposure pct: {portfolio.get('exposure_pct')}")
        positions = portfolio.get('positions') or {}
        print(f'Open positions: {(len(portfolio.positions) if isinstance(portfolio.positions, dict) else 0)}')
    if note:
        print(f'Note: {note}')
    if isinstance(features, dict) and features:
        ranked = sorted(features.items(), key=lambda item: item[1].get('signal_strength', item[1].get('confidence', 0)) if isinstance(item[1], dict) else 0, reverse=True)
        print('Top features/signals:')
        for symbol, data in ranked[:5]:
            if isinstance(data, dict):
                print(f"  {symbol} | source={data.get('source', 'NORMAL')} direction={data.get('direction')} strength={round(float(data.get('signal_strength', 0)), 4)} momentum={round(float(data.get('momentum', 0)), 5)}")
    if rejected:
        print(f'Rejected sample: {rejected[:5]}')
    print('=' * 72 + '\n')

def _feature_value(data, key, default=0.0):
    try:
        if isinstance(data, dict):
            return float(data.get(key, default) or 0.0)
    except Exception:
        pass
    return float(default or 0.0)

def _entry_signal_threshold(regime):
    r = str(regime or '').upper()
    if r == 'SIDEWAYS':
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
    source = str(data.get('source', 'NORMAL')).upper()
    return source != 'RAW_MOMENTUM_FALLBACK'

def _bullish_triple_agreement(data):
    trend = _feature_value(data, 'trend')
    momentum = _feature_value(data, 'momentum')
    one_tick = _feature_value(data, 'one_tick_momentum')
    if ENTRY_REQUIRE_TRIPLE_AGREEMENT:
        return trend > 0 and momentum > 0 and (one_tick > 0)
    return sum([trend > 0, momentum > 0, one_tick > 0]) >= 2

def _recent_stop_loss_blocked(symbol):
    """
    Extra stop-loss quarantine layer. This does not replace your existing
    symbol_quarantine table; it adds a rolling DB check so recently failed
    symbols cannot immediately consume slots again.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT COUNT(*) AS count\n                FROM trades\n                WHERE symbol = :symbol\n                  AND (\n                        strategy IN ('stop_loss', 'adaptive_stop_loss')\n                     OR side = 'sell' AND strategy LIKE '%stop_loss%'\n                  )\n                  AND created_at >= datetime('now', '+3 hours', '-' || :hours || ' hours')\n            "), {'symbol': symbol, 'hours': ENTRY_STOP_LOSS_QUARANTINE_HOURS}).mappings().first()
        return int(row['count'] or 0) > 0
    except Exception as e:
        print(f'ENTRY QUALITY quarantine check error for {symbol}: {e}')
        return False

def _entry_quality_reason(symbol, data, regime):
    """
    Returns None if allowed by quality gate, otherwise a rejection reason.
    """
    if symbol in EXCLUDED_ENTRY_SYMBOLS:
        return 'excluded_quote_or_stable_symbol'
    if not isinstance(data, dict) or not data.get('ready'):
        return 'feature_not_ready'
    if not _is_normal_feature(data):
        return 'raw_momentum_fallback_disabled'
    if _recent_stop_loss_blocked(symbol):
        return f'recent_stop_loss_quarantine_{ENTRY_STOP_LOSS_QUARANTINE_HOURS:g}h'
    signal_strength = _feature_value(data, 'signal_strength')
    min_signal = _entry_signal_threshold(regime)
    if signal_strength < min_signal:
        return f'signal_too_weak_{signal_strength:.4f}_lt_{min_signal:.4f}'
    if ENTRY_REQUIRE_LONG_TREND:
        long_trend = _feature_value(data, 'long_trend')
        if long_trend <= 0:
            return f'long_trend_not_positive_{long_trend:.4f}'
    if not _bullish_triple_agreement(data):
        trend = _feature_value(data, 'trend')
        momentum = _feature_value(data, 'momentum')
        one_tick = _feature_value(data, 'one_tick_momentum')
        return f'triple_agreement_failed_t={trend:.4f}_m={momentum:.4f}_ot={one_tick:.4f}'
    volatility = abs(_feature_value(data, 'volatility'))
    if volatility > ENTRY_MAX_VOLATILITY:
        return f'volatility_too_high_{volatility:.4f}_gt_{ENTRY_MAX_VOLATILITY:.4f}'
    expected_move = max(float(globals().get('FULL_TAKE_PROFIT_PCT', 0.0) or 0.0), float(globals().get('TAKE_PROFIT_PCT', 0.0) or 0.0))
    if expected_move < ENTRY_MIN_EXPECTED_MOVE_PCT:
        return f'expected_move_too_small_{expected_move:.4f}_lt_{ENTRY_MIN_EXPECTED_MOVE_PCT:.4f}'
    return None

def entry_quality_ranked_symbols(feature_map, regime):
    """
    Build the eligible top-N execution list from normal FeatureStore features.
    This is the core ranking gate.
    """
    eligible = []
    rejected_preview = []
    if not isinstance(feature_map, dict):
        return (set(), [], [])
    for symbol, data in feature_map.items():
        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            rejected_preview.append({'symbol': symbol, 'reason': f'entry_quality_{reason}'})
            continue
        eligible.append((symbol, _feature_value(data, 'signal_strength'), _feature_value(data, 'momentum')))
    eligible.sort(key=lambda x: (x[1], x[2]), reverse=True)
    top = eligible[:ENTRY_QUALITY_TOP_N]
    top_symbols = {s for s, _, _ in top}
    return (top_symbols, top, rejected_preview)

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
    return _sideways_exceptional_level(_feature_value(data, 'signal_strength')) > 0

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
            rows = conn.execute(text("\n                SELECT symbol, strategy, created_at\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= datetime('now', '+3 hours', '-1 hours')\n                ORDER BY created_at DESC\n            ")).mappings().all()
    except Exception as e:
        print('SIDEWAYS PACING recent buy query error:', e)
    return list(rows or [])

def _minutes_since_last_sideways_entry():
    try:
        rows = _recent_buy_rows_for_sideways()
        if not rows:
            return 9999.0
        latest = rows[0].get('created_at')
        if not latest:
            return 9999.0
        latest_s = str(latest).replace('T', ' ').split('.')[0]
        dt = datetime.fromisoformat(latest_s)
        age = (datetime.utcnow() - dt).total_seconds() / 60.0
        if age < 0:
            age = abs(age)
        return age
    except Exception as e:
        print('SIDEWAYS PACING age error:', e)
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
    if str(regime or '').upper() != 'SIDEWAYS':
        return None
    signal_strength = _feature_value(data, 'signal_strength')
    exceptional_level = _sideways_exceptional_level(signal_strength)
    is_exceptional = exceptional_level > 0
    recent_count = _sideways_recent_entry_count()
    minutes_since_last = _minutes_since_last_sideways_entry()
    current_minute = _current_hour_minute_utc()
    if is_exceptional:
        print(f'SIDEWAYS EXCEPTIONAL SIGNAL: {symbol} strength={signal_strength:.4f} level={exceptional_level} recent_count={recent_count} minutes_since_last={minutes_since_last:.1f}')
    if recent_count >= SIDEWAYS_MAX_ENTRIES_PER_HOUR:
        if not (is_exceptional and SIDEWAYS_EXCEPTIONAL_BYPASS_HOURLY_CAP):
            return f'sideways_hourly_cap_hit_{recent_count}_of_{SIDEWAYS_MAX_ENTRIES_PER_HOUR}'
    if minutes_since_last < SIDEWAYS_ENTRY_MIN_GAP_MINUTES:
        if not (is_exceptional and SIDEWAYS_EXCEPTIONAL_BYPASS_PACING):
            return f'sideways_pacing_wait_{minutes_since_last:.1f}_lt_{SIDEWAYS_ENTRY_MIN_GAP_MINUTES:g}_minutes'
    if recent_count >= max(0, SIDEWAYS_MAX_ENTRIES_PER_HOUR - 1):
        if current_minute < SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE:
            if not is_exceptional:
                return f'sideways_final_slot_reserved_until_minute_{SIDEWAYS_RESERVE_FINAL_SLOT_UNTIL_MINUTE}'
    return None

def _minutes_since_symbol_buy(symbol):
    """
    Returns minutes since this symbol was last bought.
    Used to stop repeated same-symbol clustering.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT created_at\n                FROM trades\n                WHERE LOWER(side) = 'buy'\n                  AND symbol = :symbol\n                ORDER BY created_at DESC\n                LIMIT 1\n            "), {'symbol': symbol}).mappings().first()
        if not row or not row.get('created_at'):
            return 9999.0
        latest_s = str(row.get('created_at')).replace('T', ' ').split('.')[0]
        dt = datetime.fromisoformat(latest_s)
        age = (datetime.utcnow() - dt).total_seconds() / 60.0
        if age < 0:
            age = abs(age)
        return age
    except Exception as e:
        print(f'SAME SYMBOL COOLDOWN check error for {symbol}: {e}')
        return 9999.0

def _same_symbol_cooldown_reason(symbol, data):
    """
    Normal signals wait longer before rebuying the same symbol.
    Exceptional signals get a shorter cooldown but are not unlimited.
    """
    if not symbol:
        return 'missing_symbol'
    signal_strength = _feature_value(data, 'signal_strength') if isinstance(data, dict) else 0.0
    is_exceptional = _sideways_exceptional_level(signal_strength) > 0 if '_sideways_exceptional_level' in globals() else False
    cooldown = SAME_SYMBOL_EXCEPTIONAL_COOLDOWN_MINUTES if is_exceptional else SAME_SYMBOL_ENTRY_COOLDOWN_MINUTES
    age = _minutes_since_symbol_buy(symbol)
    if age < cooldown:
        return f'same_symbol_cooldown_{age:.1f}_lt_{cooldown:g}_minutes'
    return None

def build_entry_quality_top_symbols(features: dict, top_n: int=10):
    """
    Rank normal FeatureStore symbols for entry quality.
    This fixes cases where allocator sees good symbols but ENTRY QUALITY TOP 10 is [].
    """
    ranked = []
    if not isinstance(features, dict):
        return []
    for symbol, f in features.items():
        try:
            if not isinstance(f, dict):
                continue
            if not f.get('ready'):
                continue
            source = str(f.get('source', 'NORMAL')).upper()
            if source == 'RAW_MOMENTUM_FALLBACK':
                continue
            symbol_regime = str(f.get('symbol_regime', '')).upper()
            if symbol_regime not in ('SYMBOL_TREND_UP', 'SYMBOL_BREAKOUT_UP'):
                continue
            trend = float(f.get('trend', 0.0) or 0.0)
            momentum = float(f.get('momentum', 0.0) or 0.0)
            one_tick = float(f.get('one_tick_momentum', 0.0) or 0.0)
            signal = float(f.get('signal_strength', 0.0) or 0.0)
            quality = float(f.get('trend_quality', 0.0) or 0.0)
            breakout = float(f.get('breakout_score', 0.0) or 0.0)
            if trend <= 0 or momentum <= 0 or signal <= 0:
                continue
            if one_tick < -0.0015:
                continue
            score = signal + quality + breakout
            ranked.append({'symbol': symbol, 'score': score, 'symbol_regime': symbol_regime, 'signal_strength': signal, 'trend_quality': quality, 'breakout_score': breakout})
        except Exception:
            continue
    ranked = sorted(ranked, key=lambda x: x['score'], reverse=True)
    return ranked[:int(top_n)]

def enforce_entry_quality_lockdown(result, feature_map, regime):
    """
    Final hard firewall before orders are printed/applied.
    It ensures only top-ranked, high-confluence NORMAL features can trade.
    """
    if not ENTRY_QUALITY_LOCKDOWN_ENABLED:
        return (result.get('orders', []), [])
    orders = result.get('orders', []) if isinstance(result, dict) else []
    if not orders:
        try:
            top_symbols, top_ranked, rejected_preview = entry_quality_ranked_symbols(feature_map, regime)
            print(f'ENTRY QUALITY TOP {ENTRY_QUALITY_TOP_N}:', top_ranked)
        except Exception:
            pass
        return ([], [])
    top_symbols, top_ranked, rejected_preview = entry_quality_ranked_symbols(feature_map, regime)
    print(f'ENTRY QUALITY TOP {ENTRY_QUALITY_TOP_N}:', top_ranked)
    filtered = []
    rejected = []
    for order in orders:
        symbol = order.get('symbol')
        side = str(order.get('side', '')).lower()
        strategy = str(order.get('strategy', ''))
        if side == 'sell':
            filtered.append(order)
            continue
        if strategy == 'raw_momentum_fallback':
            rejected.append({'symbol': symbol, 'reason': 'entry_quality_raw_momentum_fallback_disabled'})
            continue
        if symbol not in top_symbols:
            rejected.append({'symbol': symbol, 'reason': f'entry_quality_not_top_{ENTRY_QUALITY_TOP_N}'})
            continue
        data = feature_map.get(symbol, {})
        reason = _entry_quality_reason(symbol, data, regime)
        if reason:
            rejected.append({'symbol': symbol, 'reason': f'entry_quality_{reason}'})
            continue
        filtered.append(order)
    if rejected:
        print('ENTRY QUALITY REJECTED:', rejected[:ENTRY_QUALITY_LOG_REJECTION_LIMIT])
    return (filtered, rejected)

def main():
    positions = globals().get('positions', [])
    load_state_from_db()
    while True:
        try:
            tick = market.tick()
            raw_prices = tick['prices']
            print('MARKET TICK DATA RAW:', raw_prices)
            prices = validate_market_prices(raw_prices)
            print('MARKET TICK DATA VALIDATED:', prices)
            if not prices:
                print('MARKET DATA BLOCK: no_valid_prices_this_cycle')
                time.sleep(settings.trade_interval_seconds)
                continue
            remember_prices(prices)
            refresh_recent_trade_counts()
            features.update(prices)
            f_by_symbol = {s: features.features(s) for s in settings.symbol_list}
            ready = [f for f in f_by_symbol.values() if isinstance(f, dict) and f.get('ready')]
            fallback_features = {}
            if not ready:
                print('WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...')
                fallback_features = build_raw_momentum_fallback(prices)
                if fallback_features:
                    print('FALLBACK FEATURES DIAGNOSTIC ONLY:', fallback_features)
                else:
                    print('FALLBACK FEATURES: {}')
            avg_vol = sum((f['volatility'] for f in ready)) / len(ready) if ready else 0
            avg_trend = sum((abs(f['trend']) for f in ready)) / len(ready) if ready else 0
            regime = detect_regime(avg_vol, avg_trend)
            risk.tune(regime)
            equity = portfolio.mark_to_market(prices)
            state = {'prices': prices, 'features': f_by_symbol, 'equity': equity, 'cash': portfolio.cash, 'regime': regime}
            result = agent.run_cycle(state)
            if not isinstance(result, dict):
                result = {'orders': [], 'status': 'agent_returned_non_dict'}
            proposed_agent_orders = result.get('orders', [])
            if not proposed_agent_orders and fallback_features:
                print('FALLBACK TRADING DISABLED: diagnostic fallback ignored; waiting for normal MEXC ranked signals')
            entry_quality_rejections = []
            try:
                result['orders'], entry_quality_rejections = enforce_entry_quality_lockdown(result=result, feature_map=state['features'], regime=regime)
            except Exception as entry_quality_error:
                print('ENTRY QUALITY LOCKDOWN ERROR:', entry_quality_error)
            if not result.get('orders'):
                scout_order = qfos_build_scout_fallback_order(
                    feature_map=state['features'],
                    prices=prices,
                    regime=regime,
                    equity=equity,
                    cash=portfolio.cash,
                )
                if scout_order:
                    result['orders'] = [scout_order]
                    entry_quality_rejections = []
                    print('[SCOUT_FALLBACK] injected into proposed_fills:', scout_order)

            print('FEATURES:', {k: v for k, v in state['features'].items() if isinstance(v, dict) and v.get('ready')})
            print('ORDERS:', result.get('orders', []))
            proposed_fills = result.get('orders', [])
            # QFOS_EXPECTANCY_SAFE_APPLY
            try:
                proposed_fills = qfos_expectancy_guard_with_cycle_log(locals().get('proposed_fills', []), locals())
            except Exception as _qfos_expectancy_error:
                print('[EXPECTANCY_PATCH] guard failed safely: ' + repr(_qfos_expectancy_error))
                proposed_fills = locals().get('proposed_fills', []) or []

            applied_fills = []
            rejected = []
            try:
                if entry_quality_rejections:
                    rejected.extend(entry_quality_rejections)
            except NameError:
                pass
            entries_this_cycle = 0
            paused = is_paused()
            if globals().get('last_seen_paused_state') is True and paused is False:
                reset_liquidity_errors()
            globals()['last_seen_paused_state'] = paused
            if not paused and pause_reason():
                reset_liquidity_errors()
            if paused:
                rejected.append({'symbol': 'ALL', 'reason': pause_reason() or 'paused'})
            else:
                buys_this_cycle = 0
            proposed_fills = locals().get('proposed_fills', []) or []
            for fill in proposed_fills:
                strategy = fill.get('strategy')
                is_shadow = fill.get('shadow_mode', False)
                symbol = fill['symbol']
                side = fill['side']
                confidence = float(fill.get('confidence', 0))
                if side == 'buy':
                    allowed, reason = entry_policy_allows(symbol, regime, confidence, entries_this_cycle, strategy=strategy)
                    if not allowed:
                        rejected.append({'symbol': symbol, 'reason': reason})
                        continue
                    if is_shadow:
                        if apply_shadow_buy(fill):
                            applied_fills.append(fill)
                        continue
                    approved, reason = can_buy(symbol, fill, prices, equity)
                    if approved and apply_buy(fill):
                        applied_fills.append(fill)
                        entries_this_cycle += 1
                    else:
                        rejected.append({'symbol': symbol, 'reason': reason})
                elif side == 'sell':
                    applied_fills.append(fill)
            applied_fills.extend(generate_sells(prices, regime))
            applied_fills.extend(emergency_reduce_exposure(prices))
            equity = portfolio.mark_to_market(prices)
            exposure = total_exposure(prices)
            globals()['last_known_equity'] = equity
            globals()['last_known_exposure'] = exposure
            globals()['last_known_regime'] = regime
            if check_daily_loss_guard(equity, exposure, regime):
                proposed_fills = []
                applied_fills = []
                rejected.append({'symbol': 'ALL', 'reason': 'max_daily_loss_auto_pause'})
            with engine.begin() as conn:
                filtered_fills = []
                for fill in applied_fills:
                    allowed, reason = final_trade_firewall(fill, regime)
                    if allowed:
                        filtered_fills.append(fill)
                    else:
                        rejected.append({'symbol': fill.get('symbol', 'UNKNOWN'), 'reason': reason})
                applied_fills = filtered_fills
                for fill in applied_fills:
                    fill_pnl, original_strat = update_position_from_fill(conn, fill)
                    fill['pnl'] = fill_pnl
                    trades_total.inc()
                    conn.execute(text("\n                        INSERT INTO trades(\n                            symbol, side, quantity, expected_price, fill_price,\n            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at\n                        )\n                        VALUES(\n                            :symbol, :side, :quantity, :expected_price, :fill_price,\n                            :slippage_bps, :pnl, :strategy, :confidence, :live, :shadow_mode, DATETIME('now', '+3 hours')\n                        )\n                    "), fill | {'live': settings.live_trading, 'shadow_mode': fill.get('shadow_mode', False)})
                    side = fill.get('side', '').upper()
                    symbol = fill.get('symbol', '')
                    qty = float(fill.get('quantity', 0))
                    price = float(fill.get('fill_price', 0))
                    strategy = fill.get('strategy', 'unknown')
                    confidence = float(fill.get('confidence', 0))
                    is_shadow = fill.get('shadow_mode', False)
                    if not is_shadow:
                        pos_row = conn.execute(text('SELECT quantity FROM positions WHERE symbol = :s'), {'s': symbol}).mappings().first()
                        if pos_row:
                            portfolio.positions[symbol] = float(pos_row['quantity'])
                        else:
                            portfolio.positions[symbol] = 0.0
                    send_telegram_alert(f"<b>{side} {('(SHADOW)' if is_shadow else '')}</b> {symbol}\nQty: {qty:.6f}\nPrice: {price:.4f}\nPnL: {fill_pnl:.2f}\nStrategy: {strategy}\nConfidence: {confidence:.2f}\nLive: {settings.live_trading}")
                    score_strategy = original_strat if side == 'SELL' else strategy
                    if score_strategy and score_strategy not in ('take_profit', 'single_full_take_profit', 'breakeven_protection_exit', 'time_stop_exit', 'trailing_profit_exit', 'stop_loss', 'adaptive_take_profit', 'adaptive_stop_loss', 'risk_off_exit', 'emergency_exposure_reduction', 'unknown'):
                        conn.execute(text("\n                            INSERT INTO strategy_scores (strategy, sharpe, drawdown, score, status)\n                            VALUES (:strategy, 0, 0, :pnl, 'active')\n                            ON CONFLICT DO NOTHING\n                        "), {'strategy': score_strategy, 'pnl': fill_pnl})
                        conn.execute(text("\n                            UPDATE strategy_scores\n                            SET score = score + :pnl, status = CASE WHEN score + :pnl < 0 THEN 'blocked' ELSE 'active' END\n                            WHERE strategy = :strategy\n                        "), {'strategy': score_strategy, 'pnl': fill_pnl})
                mark_positions_to_market(conn, prices)
                conn.execute(text('\n                    INSERT INTO portfolio_snapshots(\n                        equity, cash, exposure, drawdown, regime\n                    )\n                    VALUES(\n                        :equity, :cash, :exposure, :drawdown, :regime\n                    )\n                '), {'equity': equity, 'cash': portfolio.cash, 'exposure': exposure, 'drawdown': portfolio.drawdown, 'regime': regime})
            equity_gauge.set(equity)
            drawdown_gauge.set(portfolio.drawdown)
            current_risk_status = 'SAFE'
            exposure_pct = exposure / equity if equity else 0
            if portfolio.drawdown <= -0.05 or exposure_pct >= 0.5:
                current_risk_status = 'BLOCKED'
            elif portfolio.drawdown <= -0.02 or exposure_pct >= 0.35:
                current_risk_status = 'CAUTION'
            global_last = globals().get('last_risk_status')
            if current_risk_status != global_last:
                send_telegram_alert(f'Risk status changed: <b>{current_risk_status}</b>\nEquity: {equity:.2f}\nExposure: {exposure:.2f}\nExposure %: {exposure_pct * 100:.2f}%\nDrawdown: {portfolio.drawdown:.4f}')
                globals()['last_risk_status'] = current_risk_status
            live_payload = {'name': 'Quant Fund OS', 'mode': getattr(settings, 'mode', 'paper'), 'live_trading': bool(getattr(settings, 'live_trading', False)), 'exchange': getattr(settings, 'exchange', 'mexc'), 'exchange_type': getattr(settings, 'exchange_type', 'spot'), 'regime': regime, 'risk_status': current_risk_status, 'bot_state': 'PAUSED' if paused else 'RUNNING', 'paused': bool(paused), 'pause_reason': pause_reason, 'portfolio': {'equity': equity, 'cash': portfolio.cash, 'exposure': exposure, 'exposure_pct': exposure_pct, 'regime': regime}, 'positions': portfolio.positions, 'orders': len(result.get('orders', [])) if isinstance(result, dict) else 0, 'controls': {'pause': '/pause', 'resume': '/resume', 'kill_switch': '/kill-switch'}}
            update_live_status_cache(live_payload)
            print({'regime': regime, 'equity': round(equity, 2), 'cash': round(portfolio.cash, 2), 'exposure': round(exposure, 2), 'exposure_pct': round(exposure / equity, 4) if equity else 0, 'positions': {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0}, 'orders': len(applied_fills), 'rejected': rejected[:3], 'status': result['status'], 'paused': is_paused(), 'risk_status': current_risk_status, 'shadow_positions': {k: round(v, 6) for k, v in shadow_positions.items() if v > 0}})
            try:
                diagnostic_snapshot = {'regime': regime, 'equity': round(equity, 2), 'cash': round(portfolio.cash, 2), 'exposure': round(exposure, 2), 'exposure_pct': round(exposure / equity, 4) if equity else 0, 'positions': {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0}, 'risk_status': current_risk_status}
                log_cycle_diagnostic(market_data=prices, features={k: v for k, v in state['features'].items() if isinstance(v, dict) and v.get('ready')}, orders=applied_fills, portfolio=diagnostic_snapshot, rejected=rejected, note='main_loop_after_execution')
            except Exception as diagnostic_error:
                print('DIAGNOSTIC_LOG_ERROR:', diagnostic_error)
            time.sleep(settings.trade_interval_seconds)
        except Exception as e:
            error_message = str(e)
            print('Bot loop error:', error_message)
            if 'insufficient synthetic liquidity' in error_message.lower():
                if 'for ' in error_message:
                    symbol = error_message.split('for')[-1].strip()
                    quarantine_symbol(symbol, 'liquidity_error_circuit_breaker')
                else:
                    register_liquidity_error(error_message)
            time.sleep(settings.trade_interval_seconds)
@app.get('/live-status')
def live_status():
    live = get_live_status_cache()
    if live:
        return live
    return {'name': 'Quant Fund OS', 'status': 'warming_up_or_no_live_cache', 'note': 'Live cache not populated yet. Use dashboard/logs until first loop update.'}

if __name__ == '__main__':
    main()
