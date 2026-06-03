
# RESCUE_HOURLY_CAP_PATCH_V1
# Rescue entries were capped too tightly at 2/hour.
# Start with 4/hour in SIDEWAYS. Do not jump to 6 until another clean run confirms stability.
ALLOCATOR_RESCUE_HOURLY_CAP = 4

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

# ============================================================
# QFOS OUTLIER LOSS CAP — percentage based, equity-scaled
# Purpose:
#   Prevent one bad trade from wiping out many small wins.
#   These are percentages of CURRENT equity, not fixed dollars.
#
# Examples:
#   0.0004 = 0.04% of equity
#   0.0007 = 0.07% of equity
#
# At $100:
#   SIDEWAYS cap = $0.04
#   TREND cap    = $0.07
#
# At $1000:
#   SIDEWAYS cap = $0.40
#   TREND cap    = $0.70
# ============================================================
OUTLIER_LOSS_CAP_SIDEWAYS_PCT = 0.0004
OUTLIER_LOSS_CAP_TREND_PCT = 0.0007
OUTLIER_LOSS_CAP_RISK_OFF_PCT = 0.0003

BIG_LOSS_COOLDOWN_HOURS = 6.0
CATASTROPHIC_LOSS_COOLDOWN_HOURS = 24.0
CATASTROPHIC_LOSS_MULTIPLIER = 2.5


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
exec('\n\nimport json\nimport math\nimport time\nfrom datetime import datetime, timezone\nfrom pathlib import Path\nfrom typing import Any, Dict, List\n\nROOT = Path(__file__).resolve().parent\nCONFIG_PATH = ROOT / "qfos_expectancy_config.json"\n\nDEFAULT_CONFIG = {\n    "enabled": True,\n    "sideways_scout_notional_usd": 1.00,\n    "trend_scout_notional_usd": 1.50,\n    "min_trade_price": 0.01,\n    "min_scout_signal_sideways": 0.0012,\n    "min_scout_trend_quality": 0.001,\n    "max_scout_volatility_sideways": 0.006,\n    "require_positive_one_tick_for_scout": True,\n    "fallback_stop_loss_pct": -0.006,\n    "breakeven_arm_pct": 0.0035,\n    "breakeven_exit_floor_pct": 0.0004,\n    "trailing_arm_pct": 0.005,\n    "trailing_giveback_pct": 0.0025,\n    "sideways_time_stop_minutes": 45,\n    "sideways_time_stop_min_pnl": -0.0015,\n    "sideways_time_stop_max_pnl": 0.002,\n    "symbol_cooldown_after_losses": 2,\n    "cooldown_minutes": 180,\n    "state_file": "qfos_expectancy_state.json",\n    "decision_log": "qfos_expectancy_decisions.jsonl",\n}\n\ndef _load_config() -> Dict[str, Any]:\n    try:\n        if CONFIG_PATH.exists():\n            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))\n            merged = dict(DEFAULT_CONFIG)\n            merged.update(data or {})\n            return merged\n    except Exception:\n        pass\n    return dict(DEFAULT_CONFIG)\n\nCFG = _load_config()\nSTATE_PATH = ROOT / str(CFG.get("state_file", "qfos_expectancy_state.json"))\nDECISION_LOG = ROOT / str(CFG.get("decision_log", "qfos_expectancy_decisions.jsonl"))\n\ndef _now_ts() -> float:\n    return time.time()\n\ndef _utc() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\ndef _safe_float(x: Any, default: float = 0.0) -> float:\n    try:\n        if x is None:\n            return default\n        v = float(x)\n        if math.isnan(v) or math.isinf(v):\n            return default\n        return v\n    except Exception:\n        return default\n\ndef _safe_str(x: Any, default: str = "") -> str:\n    try:\n        return str(x)\n    except Exception:\n        return default\n\ndef _read_state() -> Dict[str, Any]:\n    try:\n        if STATE_PATH.exists():\n            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))\n            if isinstance(data, dict):\n                data.setdefault("positions", {})\n                data.setdefault("losses", {})\n                return data\n    except Exception:\n        pass\n    return {"positions": {}, "losses": {}}\n\ndef _write_state(state: Dict[str, Any]) -> None:\n    try:\n        tmp = STATE_PATH.with_suffix(".tmp")\n        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")\n        tmp.replace(STATE_PATH)\n    except Exception:\n        pass\n\ndef _log(action: str, **payload: Any) -> None:\n    try:\n        row = {"ts": _utc(), "action": action}\n        row.update(payload)\n        with DECISION_LOG.open("a", encoding="utf-8") as f:\n            f.write(json.dumps(row, sort_keys=True) + "\\n")\n    except Exception:\n        pass\n\ndef _as_dict(obj: Any) -> Dict[str, Any]:\n    if isinstance(obj, dict):\n        return obj\n\n    data = {}\n    for name in (\n        "symbol", "quantity", "qty", "avg_entry", "entry_price",\n        "mark_price", "price", "strategy", "created_at", "updated_at"\n    ):\n        if hasattr(obj, name):\n            try:\n                data[name] = getattr(obj, name)\n            except Exception:\n                pass\n    return data\n\ndef _get_feature(features: Any, symbol: str) -> Dict[str, Any]:\n    if isinstance(features, dict):\n        value = features.get(symbol, {})\n        if isinstance(value, dict):\n            return value\n    return {}\n\ndef _get_price(symbol: str, pos: Dict[str, Any], feature: Dict[str, Any], prices: Any) -> float:\n    if isinstance(prices, dict) and symbol in prices:\n        p = _safe_float(prices.get(symbol))\n        if p > 0:\n            return p\n\n    p = _safe_float(feature.get("price"))\n    if p > 0:\n        return p\n\n    p = _safe_float(pos.get("mark_price"))\n    if p > 0:\n        return p\n\n    return _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price"))\n\ndef _get_positions(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:\n    raw = (\n        ctx.get("positions")\n        or ctx.get("open_positions")\n        or ctx.get("portfolio_positions")\n        or {}\n    )\n\n    out: Dict[str, Dict[str, Any]] = {}\n\n    if isinstance(raw, dict):\n        for sym, pos in raw.items():\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol") or sym)\n            if symbol:\n                out[symbol] = pd\n\n    elif isinstance(raw, list):\n        for pos in raw:\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol"))\n            if symbol:\n                out[symbol] = pd\n\n    return out\n\ndef _order_symbol(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("symbol"))\n\ndef _order_side(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("side")).lower()\n\ndef _order_strategy(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("strategy") or order.get("reason") or "unknown")\n\ndef _order_price(order: Dict[str, Any], feature: Dict[str, Any]) -> float:\n    p = _safe_float(order.get("fill_price") or order.get("expected_price") or order.get("price"))\n    if p > 0:\n        return p\n    return _safe_float(feature.get("price"))\n\ndef _recent_losses(symbol: str, state: Dict[str, Any], minutes: float) -> int:\n    losses = state.get("losses", {}).get(symbol, [])\n    now = _now_ts()\n    cutoff = now - minutes * 60\n    fresh = [x for x in losses if _safe_float(x.get("ts")) >= cutoff]\n    state.setdefault("losses", {})[symbol] = fresh\n    return len(fresh)\n\ndef _record_loss(symbol: str, strategy: str, state: Dict[str, Any]) -> None:\n    if not symbol:\n        return\n    state.setdefault("losses", {}).setdefault(symbol, []).append({\n        "ts": _now_ts(),\n        "strategy": strategy,\n    })\n\ndef _record_buy(order: Dict[str, Any], feature: Dict[str, Any], state: Dict[str, Any]) -> None:\n    symbol = _order_symbol(order)\n    if not symbol:\n        return\n\n    price = _order_price(order, feature)\n    if price <= 0:\n        return\n\n    state.setdefault("positions", {})[symbol] = {\n        "entry_ts": _now_ts(),\n        "entry_price": price,\n        "highest_price": price,\n        "highest_pnl_pct": 0.0,\n        "strategy": _order_strategy(order),\n        "signal_strength": _safe_float(order.get("signal_strength") or feature.get("signal_strength")),\n    }\n\ndef _ensure_position_meta(symbol: str, pos: Dict[str, Any], price: float, state: Dict[str, Any]) -> Dict[str, Any]:\n    positions = state.setdefault("positions", {})\n    meta = positions.get(symbol)\n\n    entry = _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price") or price)\n\n    if not isinstance(meta, dict):\n        meta = {\n            "entry_ts": _now_ts(),\n            "entry_price": entry if entry > 0 else price,\n            "highest_price": price,\n            "highest_pnl_pct": 0.0,\n            "strategy": _safe_str(pos.get("strategy") or "recovered_position"),\n            "signal_strength": 0.0,\n        }\n        positions[symbol] = meta\n\n    if price > _safe_float(meta.get("highest_price")):\n        meta["highest_price"] = price\n\n    entry_price = _safe_float(meta.get("entry_price") or entry)\n    if entry_price > 0 and price > 0:\n        pnl_pct = (price - entry_price) / entry_price\n        meta["highest_pnl_pct"] = max(_safe_float(meta.get("highest_pnl_pct")), pnl_pct)\n\n    return meta\n\ndef _make_sell(symbol: str, qty: float, price: float, reason: str) -> Dict[str, Any]:\n    return {\n        "symbol": symbol,\n        "side": "sell",\n        "quantity": qty,\n        "expected_price": price,\n        "fill_price": price,\n        "slippage_bps": 0,\n        "strategy": reason,\n        "confidence": 1.0,\n    }\n\ndef _has_pending_sell(symbol: str, orders: List[Dict[str, Any]]) -> bool:\n    for o in orders:\n        if _order_symbol(o) == symbol and _order_side(o) == "sell":\n            return True\n    return False\n\ndef _filter_and_resize_orders(\n    orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    if not orders:\n        return []\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    filtered: List[Dict[str, Any]] = []\n\n    for order in orders:\n        if not isinstance(order, dict):\n            filtered.append(order)\n            continue\n\n        symbol = _order_symbol(order)\n        side = _order_side(order)\n        strategy = _order_strategy(order)\n        feature = _get_feature(features, symbol)\n        price = _order_price(order, feature)\n\n        if side == "sell":\n            if "stop_loss" in strategy:\n                _record_loss(symbol, strategy, state)\n            filtered.append(order)\n            continue\n\n        if side != "buy":\n            filtered.append(order)\n            continue\n\n        is_scout = strategy == "fallback_scout_breakout"\n\n        if not is_scout:\n            # --- UNIVERSAL SIZE CAP for ALL strategies (not just scouts) ---\n            qty_ns = _safe_float(order.get("quantity") or order.get("qty"))\n            if qty_ns > 0 and price > 0:\n                max_ns = _safe_float(\n                    CFG.get("max_entry_notional_sideways_usd", 1.50)\n                    if "SIDEWAYS" in regime\n                    else CFG.get("max_entry_notional_trend_usd", 2.50)\n                )\n                notional_ns = qty_ns * price\n                if max_ns > 0 and notional_ns > max_ns:\n                    new_qty_ns = max_ns / price\n                    order["quantity"] = new_qty_ns\n                    if "qty" in order:\n                        order["qty"] = new_qty_ns\n                    _log("RESIZE_ENTRY", symbol=symbol, old_notional=notional_ns, new_notional=max_ns, price=price, strategy=strategy)\n            _record_buy(order, feature, state)\n            filtered.append(order)\n            continue\n\n        symbol_regime = _safe_str(feature.get("symbol_regime")).upper()\n        signal = _safe_float(order.get("signal_strength") or feature.get("signal_strength"))\n        trend_quality = _safe_float(feature.get("trend_quality") or feature.get("symbol_trend_score"))\n        volatility = _safe_float(feature.get("volatility"))\n        one_tick = _safe_float(feature.get("one_tick_momentum"))\n\n        if price <= 0 or price < _safe_float(CFG["min_trade_price"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="price_too_low", price=price, strategy=strategy)\n            continue\n\n        cooldown_losses = _recent_losses(symbol, state, _safe_float(CFG["cooldown_minutes"]))\n\n        if cooldown_losses >= int(CFG["symbol_cooldown_after_losses"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="loss_cooldown", losses=cooldown_losses, strategy=strategy)\n            continue\n\n        if "SIDEWAYS" in regime:\n            if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="not_clean_uptrend", symbol_regime=symbol_regime, strategy=strategy)\n                continue\n\n            if signal < _safe_float(CFG["min_scout_signal_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_signal", signal=signal, strategy=strategy)\n                continue\n\n            if trend_quality < _safe_float(CFG["min_scout_trend_quality"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_trend_quality", trend_quality=trend_quality, strategy=strategy)\n                continue\n\n            if volatility > _safe_float(CFG["max_scout_volatility_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="extreme_volatility", volatility=volatility, strategy=strategy)\n                continue\n\n            if bool(CFG["require_positive_one_tick_for_scout"]) and one_tick <= 0:\n                _log("BLOCK_ENTRY", symbol=symbol, reason="one_tick_not_positive", one_tick=one_tick, strategy=strategy)\n                continue\n\n        qty = _safe_float(order.get("quantity") or order.get("qty"))\n\n        if qty > 0 and price > 0:\n            max_notional = _safe_float(\n                CFG["sideways_scout_notional_usd"]\n                if "SIDEWAYS" in regime\n                else CFG["trend_scout_notional_usd"]\n            )\n            notional = qty * price\n\n            if max_notional > 0 and notional > max_notional:\n                new_qty = max_notional / price\n                order["quantity"] = new_qty\n                if "qty" in order:\n                    order["qty"] = new_qty\n                _log(\n                    "RESIZE_ENTRY",\n                    symbol=symbol,\n                    old_notional=notional,\n                    new_notional=max_notional,\n                    price=price,\n                )\n\n        _record_buy(order, feature, state)\n        filtered.append(order)\n\n    return filtered\n\ndef _defensive_exit_orders(\n    existing_orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n    prices = ctx.get("prices") or ctx.get("market_prices") or ctx.get("tick") or ctx.get("market")\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    positions = _get_positions(ctx)\n    exits: List[Dict[str, Any]] = []\n\n    for symbol, pos in positions.items():\n        if _has_pending_sell(symbol, existing_orders):\n            continue\n\n        feature = _get_feature(features, symbol)\n        price = _get_price(symbol, pos, feature, prices)\n        qty = _safe_float(pos.get("quantity") or pos.get("qty"))\n\n        if qty <= 0 or price <= 0:\n            continue\n\n        meta = _ensure_position_meta(symbol, pos, price, state)\n        entry = _safe_float(meta.get("entry_price"))\n\n        if entry <= 0:\n            continue\n\n        pnl_pct = (price - entry) / entry\n        high_pnl = _safe_float(meta.get("highest_pnl_pct"))\n        age_min = max(0.0, (_now_ts() - _safe_float(meta.get("entry_ts"), _now_ts())) / 60.0)\n        strategy = _safe_str(meta.get("strategy") or pos.get("strategy"))\n\n        if strategy == "fallback_scout_breakout" and pnl_pct <= _safe_float(CFG["fallback_stop_loss_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "adaptive_stop_loss"))\n            _record_loss(symbol, "adaptive_stop_loss", state)\n            _log("EXIT", symbol=symbol, reason="tight_scout_stop", pnl_pct=pnl_pct, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["breakeven_arm_pct"]) and pnl_pct <= _safe_float(CFG["breakeven_exit_floor_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "breakeven_protection_exit"))\n            _log("EXIT", symbol=symbol, reason="breakeven_protection", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["trailing_arm_pct"]) and (high_pnl - pnl_pct) >= _safe_float(CFG["trailing_giveback_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "trailing_profit_exit"))\n            _log("EXIT", symbol=symbol, reason="trailing_profit", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if "SIDEWAYS" in regime and age_min >= _safe_float(CFG["sideways_time_stop_minutes"]):\n            if _safe_float(CFG["sideways_time_stop_min_pnl"]) <= pnl_pct <= _safe_float(CFG["sideways_time_stop_max_pnl"]):\n                exits.append(_make_sell(symbol, qty, price, "time_stop_exit"))\n                _log("EXIT", symbol=symbol, reason="sideways_time_stop", pnl_pct=pnl_pct, age_min=age_min)\n                continue\n\n    return exits\n\ndef qfos_expectancy_cycle_guard(proposed_fills: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:\n    """\n    Defensive expectancy guard.\n\n    It does four things:\n    1. Filters weak fallback scout entries.\n    2. Reduces SIDEWAYS scout size.\n    3. Adds breakeven, trailing, tight-stop, and time-stop sell orders.\n    4. Keeps risk_off_exit logic untouched.\n    """\n\n    if not CFG.get("enabled", True):\n        return proposed_fills if isinstance(proposed_fills, list) else []\n\n    state = _read_state()\n\n    try:\n        orders = list(proposed_fills or [])\n    except Exception:\n        orders = []\n\n    try:\n        orders = _filter_and_resize_orders(orders, context, state)\n        exits = _defensive_exit_orders(orders, context, state)\n\n        if exits:\n            orders = exits + orders\n\n    except Exception as exc:\n        _log("ERROR", error=repr(exc))\n\n    _write_state(state)\n    return orders\n\nprint("QFOS expectancy patch helper loaded.")\n', _QFOS_EXPECTANCY_NS)
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

        # QFOS FALLBACK SCOUT QUALITY GUARD — inline pre-expectancy filter
        try:
            _qfos_fb_locals = locals()
            for _qfos_fb_name in ("orders", "proposed_fills", "fills", "proposed_orders"):
                if _qfos_fb_name in _qfos_fb_locals and isinstance(_qfos_fb_locals[_qfos_fb_name], list):
                    _qfos_fb_locals[_qfos_fb_name][:] = _qfos_fb_filter_orders(
                        _qfos_fb_locals[_qfos_fb_name],
                        local_vars=_qfos_fb_locals,
                        source=f"inline:{_qfos_fb_name}",
                    )
        except Exception as _qfos_fb_inline_error:
            print(f"[FALLBACK_QUALITY_GUARD] inline_error={_qfos_fb_inline_error}", flush=True)

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

      # QFOS FALLBACK SCOUT QUALITY GUARD — inline pre-expectancy filter
      try:
          _qfos_fb_locals = locals()
          for _qfos_fb_name in ("orders", "proposed_fills", "fills", "proposed_orders"):
              if _qfos_fb_name in _qfos_fb_locals and isinstance(_qfos_fb_locals[_qfos_fb_name], list):
                  _qfos_fb_locals[_qfos_fb_name][:] = _qfos_fb_filter_orders(
                      _qfos_fb_locals[_qfos_fb_name],
                      local_vars=_qfos_fb_locals,
                      source=f"inline:{_qfos_fb_name}",
                  )
      except Exception as _qfos_fb_inline_error:
          print(f"[FALLBACK_QUALITY_GUARD] inline_error={_qfos_fb_inline_error}", flush=True)

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

        # QFOS FALLBACK SCOUT QUALITY GUARD — inline pre-expectancy filter
        try:
            _qfos_fb_locals = locals()
            for _qfos_fb_name in ("orders", "proposed_fills", "fills", "proposed_orders"):
                if _qfos_fb_name in _qfos_fb_locals and isinstance(_qfos_fb_locals[_qfos_fb_name], list):
                    _qfos_fb_locals[_qfos_fb_name][:] = _qfos_fb_filter_orders(
                        _qfos_fb_locals[_qfos_fb_name],
                        local_vars=_qfos_fb_locals,
                        source=f"inline:{_qfos_fb_name}",
                    )
        except Exception as _qfos_fb_inline_error:
            print(f"[FALLBACK_QUALITY_GUARD] inline_error={_qfos_fb_inline_error}", flush=True)

        print("[EXPECTANCY_PATCH] jsonl write failed: " + repr(log_exc))

    return after_list
# QFOS_EXPECTANCY_EARLY_HOOK_END

# QFOS_EXPECTANCY_INLINE_END

# ============================================================
# QFOS_PROFIT_ENGINE_SIDEWAYS_GUARD_V2_FIXED
# Hard SIDEWAYS exposure + stale-class exit guard
# ============================================================

QFOS_SIDEWAYS_HARD_EXPOSURE_PCT = globals().get("QFOS_SIDEWAYS_HARD_EXPOSURE_PCT", 0.045)
QFOS_STALE_CLASS_FROM = globals().get("QFOS_STALE_CLASS_FROM", "QUALITY_TREND_OR_BREAKOUT")
QFOS_STALE_CLASS_TO = globals().get("QFOS_STALE_CLASS_TO", "SIDEWAYS_SCALP")


def _qfos_guard_float(v, default=0.0):
    try:
        if v is None:
            return default
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _qfos_guard_get(obj, key, default=None):
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default


def _qfos_guard_overexposed(regime, exposure_pct):
    return (
        str(regime or "").upper() == "SIDEWAYS"
        and _qfos_guard_float(exposure_pct) > _qfos_guard_float(QFOS_SIDEWAYS_HARD_EXPOSURE_PCT, 0.045)
    )


def _qfos_guard_filter_new_buys(proposed_fills, regime, exposure_pct):
    """
    Final execution-stage veto.
    The allocator may still rank/select, but execution refuses new BUYs
    when SIDEWAYS exposure is already above the hard cap.
    """
    try:
        if not _qfos_guard_overexposed(regime, exposure_pct):
            return proposed_fills

        before = len(proposed_fills or [])
        kept = []
        blocked = []

        for fill in proposed_fills or []:
            if isinstance(fill, dict):
                side = str(fill.get("side", "")).lower()
                symbol = str(fill.get("symbol", "UNKNOWN"))
            else:
                side = str(_qfos_guard_get(fill, "side", "")).lower()
                symbol = str(_qfos_guard_get(fill, "symbol", "UNKNOWN"))

            if side in ("buy", "long", "open"):
                blocked.append(symbol)
                continue

            kept.append(fill)

        if blocked:
            print(
                f"[PROFIT_ENGINE_GUARD] ENTRY_BLOCKED regime={regime} "
                f"exposure_pct={_qfos_guard_float(exposure_pct):.4f} "
                f"limit={_qfos_guard_float(QFOS_SIDEWAYS_HARD_EXPOSURE_PCT):.4f} "
                f"blocked={blocked}",
                flush=True,
            )

        print(
            f"[PROFIT_ENGINE_GUARD] execution_filter before={before} after={len(kept)}",
            flush=True,
        )

        return kept
    except Exception as exc:
        print(f"[PROFIT_ENGINE_GUARD] filter_error={exc}", flush=True)
        return proposed_fills


def _qfos_guard_downgrade_trade_class(symbol, trade_class, current_policy):
    """
    If a position was QUALITY_TREND_OR_BREAKOUT but current Policy V2 now says
    SIDEWAYS_SCALP, immediately handle it as a scalp in Profit Engine exits.
    """
    tc = str(trade_class or "").upper()
    cp = str(current_policy or "").upper()

    if tc == str(QFOS_STALE_CLASS_FROM).upper() and cp == str(QFOS_STALE_CLASS_TO).upper():
        print(
            f"[PROFIT_ENGINE_GUARD] STALE_CLASS_DOWNGRADED "
            f"symbol={symbol} from={QFOS_STALE_CLASS_FROM} to={QFOS_STALE_CLASS_TO}",
            flush=True,
        )
        return QFOS_STALE_CLASS_TO

    return trade_class


def _qfos_guard_current_policy(symbol, strategy="", latest_buy_strategy="", global_regime="SIDEWAYS", exposure_pct=0.0):
    try:
        if "_qfos_v2_symbol_quality_class" in globals() and callable(globals().get("_qfos_v2_symbol_quality_class")):
            return str(_qfos_v2_symbol_quality_class(
                symbol,
                strategy=f"{strategy or ''} {latest_buy_strategy or ''}".strip(),
                global_regime=global_regime,
                exposure_pct=exposure_pct,
            ) or "").upper()
    except Exception as exc:
        print(f"[PROFIT_ENGINE_GUARD] policy_v2_lookup_error symbol={symbol} err={exc}", flush=True)

    if str(global_regime or "").upper() == "SIDEWAYS":
        return "SIDEWAYS_SCALP"

    return None


def _qfos_guard_log_watchdog(regime, exposure_pct, symbol=None, action="HOLD", reason=""):
    print(
        f"[PROFIT_ENGINE_GUARD] WATCHDOG action={action} "
        f"regime={regime} exposure_pct={_qfos_guard_float(exposure_pct):.4f} "
        f"symbol={symbol} reason={reason}",
        flush=True,
    )


def _qfos_guard_profit_engine_reduce_if_needed(cur, rows, equity, global_regime, now_s):
    """
    Profit Engine watchdog.
    If SIDEWAYS exposure is above 4.5%, close exactly one weakest stale/losing
    position before normal per-position exit handling.
    """
    try:
        equity = _qfos_guard_float(equity)
        if equity <= 0:
            return False

        total_exposure = 0.0
        prepared = []

        for row in rows or []:
            pos = dict(row)
            symbol = str(pos.get("symbol") or "")
            qty = abs(_qfos_guard_float(pos.get("quantity")))
            avg_entry = _qfos_guard_float(pos.get("avg_entry"))
            last_price = _qfos_guard_float(pos.get("last_price"))
            exposure = _qfos_guard_float(pos.get("exposure"))

            if qty <= 0 or avg_entry <= 0 or last_price <= 0:
                continue

            if exposure <= 0:
                exposure = qty * last_price

            total_exposure += exposure
            prepared.append((pos, symbol, qty, avg_entry, last_price, exposure))

        exposure_pct = total_exposure / max(equity, 1e-12)

        if not _qfos_guard_overexposed(global_regime, exposure_pct):
            return False

        candidates = []

        for pos, symbol, qty, avg_entry, last_price, exposure in prepared:
            strategy = str(pos.get("strategy") or "")
            latest_buy_strategy = None

            try:
                buy = _qfos_pe_latest_buy(cur, symbol)
                if buy:
                    latest_buy_strategy = buy["strategy"]
            except Exception:
                latest_buy_strategy = None

            try:
                trade_class = _qfos_pe_classify(
                    symbol,
                    strategy,
                    latest_buy_strategy,
                    exposure,
                    equity,
                    global_regime,
                )
            except Exception:
                trade_class = "SIDEWAYS_SCALP" if str(global_regime).upper() == "SIDEWAYS" else "QUALITY_TREND_OR_BREAKOUT"

            current_policy = _qfos_guard_current_policy(
                symbol,
                strategy=strategy,
                latest_buy_strategy=latest_buy_strategy,
                global_regime=global_regime,
                exposure_pct=exposure_pct,
            )

            effective_class = _qfos_guard_downgrade_trade_class(symbol, trade_class, current_policy)

            unrealized = _qfos_pe_unrealized(qty, avg_entry, last_price, pos.get("unrealized_pnl"))
            ret_pct = _qfos_pe_return_pct(avg_entry, last_price)

            stale = (
                str(trade_class).upper() == str(QFOS_STALE_CLASS_FROM).upper()
                and str(effective_class).upper() == str(QFOS_STALE_CLASS_TO).upper()
            )
            loser = unrealized < 0 or ret_pct < 0

            if not stale and not loser:
                continue

            score = 0.0
            if stale:
                score += 1000.0
            if loser:
                score += 500.0
            if ret_pct < 0:
                score += abs(ret_pct) * 10000.0
            if unrealized < 0:
                score += abs(unrealized) * 100.0

            candidates.append({
                "score": score,
                "pos": pos,
                "symbol": symbol,
                "qty": qty,
                "unrealized": unrealized,
                "ret_pct": ret_pct,
                "trade_class": trade_class,
                "effective_class": effective_class,
            })

        if not candidates:
            _qfos_guard_log_watchdog(
                global_regime,
                exposure_pct,
                action="BLOCK_NEW_ENTRIES_ONLY",
                reason="over_cap_no_stale_or_losing_position",
            )
            return False

        weakest = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]

        _qfos_guard_log_watchdog(
            global_regime,
            exposure_pct,
            symbol=weakest["symbol"],
            action="CLOSE_WEAKEST",
            reason=(
                f"sideways_hard_exposure_guard "
                f"class={weakest['trade_class']} effective={weakest['effective_class']} "
                f"ret_pct={weakest['ret_pct']:.6f} unrealized={weakest['unrealized']:.6f}"
            ),
        )

        _qfos_pe_sell(
            cur,
            weakest["pos"],
            weakest["qty"],
            "sideways_hard_exposure_guard",
            weakest["unrealized"],
            now_s,
            quarantine=False,
        )
        return True

    except Exception as exc:
        print(f"[PROFIT_ENGINE_GUARD] reducer_error={exc}", flush=True)
        return False

# ============================================================
# END QFOS_PROFIT_ENGINE_SIDEWAYS_GUARD_V2_FIXED
# ============================================================



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
FULL_TAKE_PROFIT_PCT = float(getattr(settings, 'full_take_profit_pct', 0.015))
BREAKEVEN_TRIGGER_PCT = float(getattr(settings, 'breakeven_trigger_pct', 0.005))
BREAKEVEN_EXIT_PCT = float(getattr(settings, 'breakeven_exit_pct', 0.0004))
TIME_STOP_MINUTES = float(getattr(settings, 'position_time_stop_minutes', 40))
TIME_STOP_EXIT_BELOW_PCT = float(getattr(settings, 'time_stop_exit_below_pct', 0.0015))
WIN_RATE_TRAIL_TRIGGER_PCT = float(getattr(settings, 'win_rate_trail_trigger_pct', 0.007))
WIN_RATE_TRAIL_GIVEBACK_PCT = float(getattr(settings, 'win_rate_trail_giveback_pct', 0.0035))
WIN_RATE_SIDEWAYS_FULL_TP_PCT = float(getattr(settings, 'win_rate_sideways_full_tp_pct', 0.0075))
WIN_RATE_MIN_HOLD_BEFORE_BREAKEVEN_MIN = float(getattr(settings, 'win_rate_min_hold_before_breakeven_min', 8))

TAKE_PROFIT_SELL_FRACTION = 1.0  # QFOS_FULL_PROFIT_MODE: close full position on take-profitngs.take_profit_sell_fraction)
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


def _qfos_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_get(obj, key, default=None):
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default


def _qfos_current_equity(portfolio=None, default=100.0):
    """
    Return current account equity.
    This keeps risk controls percentage-based and scalable across
    $10, $50, $100, $500, $1000+ accounts.
    """
    try:
        if portfolio is not None:
            equity = _qfos_float(_qfos_get(portfolio, "equity", None), 0.0)
            if equity > 0:
                return equity

        # Common globals used in simple paper-trading loops.
        for name in ("portfolio", "state", "account", "paper_portfolio"):
            obj = globals().get(name)
            if obj is None:
                continue
            equity = _qfos_float(_qfos_get(obj, "equity", None), 0.0)
            if equity > 0:
                return equity
    except Exception:
        pass

    return default


def _qfos_outlier_loss_cap_pct(regime):
    regime_upper = str(regime or "").upper()
    if regime_upper == "RISK_OFF":
        return OUTLIER_LOSS_CAP_RISK_OFF_PCT
    if regime_upper == "SIDEWAYS":
        return OUTLIER_LOSS_CAP_SIDEWAYS_PCT
    return OUTLIER_LOSS_CAP_TREND_PCT


def _qfos_outlier_loss_cap_usd(regime, equity):
    equity = max(_qfos_float(equity, 100.0), 1.0)
    return equity * _qfos_outlier_loss_cap_pct(regime)


def _qfos_position_unrealized_loss_usd(position, mark_price):
    """
    Long spot position loss:
      loss = (entry - mark) * quantity

    Returns positive USD loss only.
    """
    qty = abs(_qfos_float(
        _qfos_get(position, "quantity", _qfos_get(position, "qty", 0.0)),
        0.0
    ))
    entry = _qfos_float(
        _qfos_get(position, "avg_entry", _qfos_get(position, "entry_price", 0.0)),
        0.0
    )
    mark = _qfos_float(mark_price, 0.0)

    if qty <= 0 or entry <= 0 or mark <= 0:
        return 0.0

    loss = (entry - mark) * qty
    return max(loss, 0.0)


def _qfos_outlier_loss_exit_reason(position, mark_price, regime, portfolio=None):
    """
    Percentage-based kill guard for individual losing trades.

    This does NOT reduce trade frequency.
    It only prevents one position from becoming large enough to wipe out
    several normal small wins.
    """
    try:
        equity = _qfos_current_equity(portfolio=portfolio, default=100.0)
        cap_usd = _qfos_outlier_loss_cap_usd(regime, equity)
        loss_usd = _qfos_position_unrealized_loss_usd(position, mark_price)

        if loss_usd <= 0:
            return None

        symbol = _qfos_get(position, "symbol", "UNKNOWN")
        loss_pct_equity = loss_usd / max(equity, 1.0)

        if loss_usd >= cap_usd:
            catastrophic_cap = cap_usd * CATASTROPHIC_LOSS_MULTIPLIER
            reason = "catastrophic_outlier_loss_cap" if loss_usd >= catastrophic_cap else "outlier_loss_cap"

            print(
                "[OUTLIER_LOSS_CAP] "
                f"exit {symbol} reason={reason} "
                f"loss_usd={loss_usd:.6f} "
                f"equity={equity:.2f} "
                f"loss_pct_equity={loss_pct_equity:.6%} "
                f"cap_usd={cap_usd:.6f} "
                f"cap_pct={_qfos_outlier_loss_cap_pct(regime):.6%} "
                f"regime={regime}",
                flush=True,
            )
            return reason

        return None
    except Exception as exc:
        print(f"[OUTLIER_LOSS_CAP] guard_error={exc}", flush=True)
        return None


def _qfos_exit_decision(symbol, change, peak_change, age_minutes, regime):

    # QFOS OUTLIER LOSS CAP:
    # Equity-scaled per-trade loss guard.
    # This runs before normal adaptive stop-loss/take-profit logic.
    try:
        _qfos_mark_price = locals().get("mark_price", None)
        if _qfos_mark_price is None:
            _qfos_mark_price = locals().get("price", None)
        if _qfos_mark_price is None:
            _qfos_mark_price = locals().get("current_price", None)

        _qfos_position = locals().get("position", None)
        if _qfos_position is None:
            _qfos_position = locals().get("pos", None)

        _qfos_regime = locals().get("regime", globals().get("regime", "SIDEWAYS"))
        _qfos_portfolio = locals().get("portfolio", globals().get("portfolio", None))

        if _qfos_position is not None and _qfos_mark_price is not None:
            _qfos_outlier_reason = _qfos_outlier_loss_exit_reason(
                _qfos_position,
                _qfos_mark_price,
                _qfos_regime,
                portfolio=_qfos_portfolio,
            )
            if _qfos_outlier_reason:
                return _qfos_outlier_reason
    except Exception as _qfos_outlier_exc:
        print(f"[OUTLIER_LOSS_CAP] decision_hook_error={_qfos_outlier_exc}", flush=True)

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
    # Grace period: do NOT fire adaptive stop in the first 2 minutes unless catastrophic (>5% loss).
    # This prevents noise-triggered instant stops that hit within 60s of entry.
    STOP_GRACE_MINUTES = 2.0
    CATASTROPHIC_STOP_PCT = 0.05
    if change <= -float(dynamic_stop_pct):
        if age_minutes >= STOP_GRACE_MINUTES or change <= -CATASTROPHIC_STOP_PCT:
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
    
    pacing_reason = _sideways_pacing_reason(symbol, data, regime)
    if pacing_reason:
        return pacing_reason

    same_symbol_reason = _same_symbol_cooldown_reason(symbol, data)
    if same_symbol_reason:
        return same_symbol_reason

    return None

def _compute_quality_score(data: dict) -> float:
    """
    Cross-sectional composite quality score for SIDEWAYS candidate ranking.
    Higher is better. Used to select the BEST available symbol, not just the first.
    """
    try:
        signal   = float(_feature_value(data, 'signal_strength') or 0.0)
        one_tick = float(_feature_value(data, 'one_tick_momentum') or 0.0)
        trend    = float(_feature_value(data, 'trend') or 0.0)
        long_t   = float(_feature_value(data, 'long_trend') or 0.0)
        vol      = abs(float(_feature_value(data, 'volatility') or 0.0))
        score = (
            signal   * 2.0    # primary driver
            + one_tick * 1.5  # breakout confirmation
            + trend   * 1.0   # directional alignment
            + long_t  * 0.5   # macro backing
            - vol     * 2.0   # penalise choppiness
        )
        return round(score, 6)
    except Exception:
        return 0.0

def entry_quality_ranked_symbols(feature_map, regime):
    """
    Build the eligible top-N execution list from normal FeatureStore features.
    Candidates are ranked by composite quality score, not raw signal alone.
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
        quality_score = _compute_quality_score(data)
        signal = _feature_value(data, 'signal_strength')
        eligible.append((symbol, quality_score, signal))
    # Sort by composite quality score descending; signal breaks ties.
    eligible.sort(key=lambda x: (x[1], x[2]), reverse=True)
    top = eligible[:ENTRY_QUALITY_TOP_N]
    top_symbols = {s for s, _, _ in top}
    print(f'[QUALITY_RANK] top-{len(top)}: ' + ', '.join(
        f'{s}(qs={qs:.4f},sig={sig:.4f})' for s, qs, sig in top[:5]
    ))
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
        from datetime import timedelta
        age = ((datetime.utcnow() + timedelta(hours=3)) - dt).total_seconds() / 60.0
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

# Strategy Ladder: signal thresholds escalate as hourly slots are consumed.
# Slot 1 (first buy): easy entry at base. Slot 2: stronger required. Slot 3: only conviction.
SIDEWAYS_SLOT_THRESHOLDS = [0.0016, 0.0022, 0.0035]

def _sideways_slot_threshold(recent_count: int) -> float:
    """Return the minimum signal required for the next available SIDEWAYS slot."""
    slot = min(int(recent_count or 0), len(SIDEWAYS_SLOT_THRESHOLDS) - 1)
    return SIDEWAYS_SLOT_THRESHOLDS[slot]


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
    # Strategy Ladder: apply slot-tiered threshold (escalates as slots are consumed).
    slot_threshold = _sideways_slot_threshold(recent_count)
    if signal_strength < slot_threshold:
        if not is_exceptional:
            return f'sideways_slot_{recent_count + 1}_requires_{slot_threshold:.4f}_got_{signal_strength:.4f}'
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
        from datetime import timedelta
        age = ((datetime.utcnow() + timedelta(hours=3)) - dt).total_seconds() / 60.0
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
            # POLICY V2 FIXED — persist normal feature map immediately after feature build
            try:
                _qfos_v2_upsert_feature_snapshot(f_by_symbol)
            except Exception as _qfos_v2_feature_error:
                print(f"[POLICY_V2] feature_snapshot_fixed_error={_qfos_v2_feature_error}", flush=True)

            ready = [f for f in f_by_symbol.values() if isinstance(f, dict) and f.get('ready')]
            fallback_features = {}
            if not ready:
                print('WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...')
                fallback_features = build_raw_momentum_fallback(prices)
                if fallback_features:
                    print('FALLBACK FEATURES DIAGNOSTIC ONLY:', fallback_features)
                else:

                    # POLICY V2 — persist current feature map for trade classification
                    try:
                        if "features" in locals() and isinstance(features, dict):
                            _qfos_v2_upsert_feature_snapshot(features)
                    except Exception as _qfos_v2_feature_error:
                        print(f"[POLICY_V2] feature_snapshot_inline_error={_qfos_v2_feature_error}", flush=True)

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

            # QFOS ALLOCATOR OPPORTUNITY RESCUE — upstream rescue before fallback scout
            try:
                if isinstance(result, dict):
                    _qfos_ar_orders = result.get("orders") or []
                    if isinstance(_qfos_ar_orders, list) and len(_qfos_ar_orders) == 0:
                        _qfos_ar_rescued = _qfos_allocator_opportunity_rescue(result, locals())
                        if isinstance(_qfos_ar_rescued, list) and len(_qfos_ar_rescued) > 0:
                            result["orders"] = _qfos_ar_rescued
            except Exception as _qfos_ar_inline_error:
                print(f"[ALLOCATOR_RESCUE] inline_error={_qfos_ar_inline_error}", flush=True)

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
                    # POLICY V2 FIXED — source-block weak fallback immediately after injection
                    try:
                        if isinstance(result, dict) and isinstance(result.get('orders'), list):
                            result['orders'] = _qfos_v2_filter_fallback_orders(
                                result.get('orders', []),
                                source='result.orders.post_scout_injection',
                            )
                    except Exception as _qfos_v2_filter_error:
                        print(f"[POLICY_V2] post_scout_filter_error={_qfos_v2_filter_error}", flush=True)


                    # QFOS FALLBACK SCOUT SOURCE GUARD — directly after scout injection
                    try:
                        _qfos_fbs_locals = locals()
                        for _qfos_fbs_name in ("proposed_fills", "orders", "fills", "proposed_orders"):
                            if _qfos_fbs_name in _qfos_fbs_locals and isinstance(_qfos_fbs_locals[_qfos_fbs_name], list):
                                _qfos_before_len = len(_qfos_fbs_locals[_qfos_fbs_name])
                                _qfos_fbs_locals[_qfos_fbs_name][:] = _qfos_fbs_filter_proposed_fills(
                                    _qfos_fbs_locals[_qfos_fbs_name],
                                    local_vars=_qfos_fbs_locals,
                                    source=f"post_scout:{_qfos_fbs_name}",
                                )
                                _qfos_after_len = len(_qfos_fbs_locals[_qfos_fbs_name])
                                if _qfos_before_len != _qfos_after_len:
                                    print(
                                        f"[FALLBACK_SOURCE_GUARD] removed {_qfos_before_len - _qfos_after_len} fallback order(s) from {_qfos_fbs_name}",
                                        flush=True,
                                    )
                    except Exception as _qfos_fbs_inline_error:
                        print(f"[FALLBACK_SOURCE_GUARD] inline_error={_qfos_fbs_inline_error}", flush=True)


            print('FEATURES:', {k: v for k, v in state['features'].items() if isinstance(v, dict) and v.get('ready')})

            # POLICY V2 — block weak fallback scout before ORDERS/EXPECTANCY
            try:
                _qfos_v2_locals = locals()
                for _qfos_v2_name in ("proposed_fills", "orders", "fills", "proposed_orders"):
                    if _qfos_v2_name in _qfos_v2_locals and isinstance(_qfos_v2_locals[_qfos_v2_name], list):
                        _qfos_v2_locals[_qfos_v2_name][:] = _qfos_v2_filter_fallback_orders(
                            _qfos_v2_locals[_qfos_v2_name],
                            source=f"pre_orders:{_qfos_v2_name}",
                        )
            except Exception as _qfos_v2_filter_error:
                print(f"[POLICY_V2] fallback_filter_inline_error={_qfos_v2_filter_error}", flush=True)

            # POLICY V2 FIXED — block weak fallback orders in the real result['orders'] list
            try:
                if isinstance(result, dict) and isinstance(result.get('orders'), list):
                    result['orders'] = _qfos_v2_filter_fallback_orders(
                        result.get('orders', []),
                        source='result.orders.pre_print',
                    )
            except Exception as _qfos_v2_filter_error:
                print(f"[POLICY_V2] result_orders_filter_error={_qfos_v2_filter_error}", flush=True)

            # ============================================================
            # QFOS EXECUTION HANDOFF PATCH
            # Purpose:
            #   Keep the exact list that survived Policy V2 and pass it to
            #   the execution loop. Previously allowed result["orders"] could
            #   print in ORDERS but still not become applied fills.
            # ============================================================
            raw_result_orders = []
            try:
                if isinstance(result, dict) and isinstance(result.get('orders'), list):
                    raw_result_orders = _qfos_normalize_fill_list(list(result.get('orders') or []))
            except Exception:
                raw_result_orders = []

            print('ORDERS:', raw_result_orders)

            proposed_fills = list(raw_result_orders or [])

            # Run expectancy guard, but never lose the raw order list silently.
            try:
                proposed_fills = qfos_expectancy_guard_with_cycle_log(proposed_fills, locals())
                proposed_fills = _qfos_normalize_fill_list(list(proposed_fills or []))
            except Exception as _qfos_expectancy_error:
                print('[EXPECTANCY_PATCH] guard failed safely: ' + repr(_qfos_expectancy_error), flush=True)
                proposed_fills = list(raw_result_orders or [])

            # Final Policy V2 fallback filter on the actual execution list.
            try:
                if "_qfos_v2_filter_fallback_orders" in globals():
                    proposed_fills = _qfos_v2_filter_fallback_orders(
                        list(proposed_fills or []),
                        source="execution_handoff.final",
                    )
            except Exception as _qfos_exec_policy_error:
                print(f"[EXECUTION_STAGE] policy_filter_error={_qfos_exec_policy_error}", flush=True)

            # Keep result["orders"] synchronized with what execution will process.
            try:
                if isinstance(result, dict):
                    result["orders"] = list(proposed_fills or [])
            except Exception:
                pass

            print(
                f"[EXECUTION_STAGE] handoff raw_orders={len(raw_result_orders)} "
                f"proposed_fills={len(proposed_fills or [])} "
                f"symbols={[x.get('symbol') for x in proposed_fills if isinstance(x, dict)]}",
                flush=True,
            )

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
            proposed_fills = _qfos_normalize_fill_list(list(proposed_fills or []))

            # QFOS_SIDEWAYS_HARD_EXPOSURE_ENTRY_VETO_V2
            try:
                _qfos_entry_exposure_pct = 0.0
                try:
                    _qfos_entry_exposure_pct = float(exposure) / max(float(equity), 1e-12)
                except Exception:
                    _qfos_entry_exposure_pct = 0.0

                proposed_fills = _qfos_guard_filter_new_buys(
                    proposed_fills,
                    regime,
                    _qfos_entry_exposure_pct,
                )
                proposed_fills = _qfos_normalize_fill_list(list(proposed_fills or []))
            except Exception as _qfos_entry_guard_error:
                print(f"[PROFIT_ENGINE_GUARD] entry_veto_error={_qfos_entry_guard_error}", flush=True)

            print(f"[EXECUTION_STAGE] begin_apply proposed_fills={len(proposed_fills)}", flush=True)
            for fill in proposed_fills:
                fill = _qfos_normalize_fill_defaults(fill)
                strategy = fill.get('strategy')
                is_shadow = fill.get('shadow_mode', False)
                symbol = fill['symbol']
                side = fill['side']
                confidence = float(fill.get('confidence', 0))
                if side == 'buy':
                    allowed, reason = entry_policy_allows(symbol, regime, confidence, entries_this_cycle, strategy=strategy)

                    # Opportunity Mode: allow ranked evo_* trades through soft pacing blocks
                    # when account is SAFE/low exposure. Never overrides hard safety blocks.
                    if not allowed:
                        try:
                            if _qfos_opp_can_override_entry_reject(symbol, strategy, confidence, reason, fill):
                                allowed = True
                                reason = "opportunity_mode_override"
                        except Exception as _qfos_opp_override_error:
                            print(f"[OPPORTUNITY_MODE] override_error symbol={symbol} err={_qfos_opp_override_error}", flush=True)

                    if not allowed:
                        print(
                            f"[EXECUTION_STAGE] entry_policy_rejected symbol={symbol} "
                            f"strategy={strategy} reason={reason}",
                            flush=True,
                        )
                        rejected.append({'symbol': symbol, 'reason': reason})
                        continue
                    if is_shadow:
                        if apply_shadow_buy(fill):
                            applied_fills.append(fill)
                        continue
                    # Opportunity Mode tiered sizing before can_buy.
                    try:
                        fill = _qfos_opp_resize_fill(fill, equity=equity)
                    except Exception as _qfos_opp_resize_error:
                        print(f"[OPPORTUNITY_MODE] resize_error symbol={symbol} err={_qfos_opp_resize_error}", flush=True)

                    approved, reason = can_buy(symbol, fill, prices, equity)
                    print(
                        f"[EXECUTION_STAGE] can_buy symbol={symbol} approved={approved} "
                        f"reason={reason} qty={fill.get('quantity')} price={fill.get('fill_price')} "
                        f"tier={fill.get('qfos_size_tier')} value={fill.get('qfos_target_value')}",
                        flush=True,
                    )
                    if approved:
                        applied_ok = apply_buy(fill)
                        print(
                            f"[EXECUTION_STAGE] apply_buy symbol={symbol} applied={applied_ok}",
                            flush=True,
                        )
                        if applied_ok:
                            applied_fills.append(fill)
                            entries_this_cycle += 1
                        else:
                            rejected.append({'symbol': symbol, 'reason': 'apply_buy_failed'})
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
                applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                applied_fills = _qfos_full_exit_filter_fills(applied_fills)
                for fill in applied_fills:
                    allowed, reason = final_trade_firewall(fill, regime)
                    if allowed:
                        filtered_fills.append(fill)
                    else:
                        print(
                            f"[EXECUTION_STAGE] final_firewall_rejected "
                            f"symbol={fill.get('symbol', 'UNKNOWN')} strategy={fill.get('strategy')} reason={reason}",
                            flush=True,
                        )
                        rejected.append({'symbol': fill.get('symbol', 'UNKNOWN'), 'reason': reason})
                applied_fills = filtered_fills
                print(f"[EXECUTION_STAGE] final_applied_fills={len(applied_fills)}", flush=True)
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

                    print(
                        f"[EXECUTION_STAGE] db_trade_written side={side} symbol={symbol} "
                        f"qty={qty:.8f} price={price:.8f} pnl={fill_pnl:.6f} "
                        f"position_qty={portfolio.positions.get(symbol, 0.0)}",
                        flush=True,
                    )
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

# ============================================================
# METRIC_TRUTH_STOPLOSS_WINRATE_V1
# Dashboard / status metric truth helpers.
#
# Purpose:
# - Count ALL stop-loss exits, not only exact "stop_loss".
# - sideways_scalp_stop_loss, quality_initial_stop_loss, etc.
#   must be included in total stop losses.
# - Win rate must be calculated from completed exits:
#       take_profit_count / (take_profit_count + stop_loss_count)
#   when explicit exit labels are available.
# ============================================================

def qfo_safe_lower(value):
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""

def qfo_trade_get(trade, key, default=None):
    try:
        if isinstance(trade, dict):
            return trade.get(key, default)
        return getattr(trade, key, default)
    except Exception:
        return default

def qfo_trade_label(trade):
    """
    Return a normalized label for why/how a trade happened.
    Supports both dict trades and object/ORM trades.
    """
    fields = (
        "strategy",
        "exit_reason",
        "reason",
        "close_reason",
        "exit_type",
        "signal",
        "tag",
        "comment",
    )
    parts = []
    for field in fields:
        value = qfo_trade_get(trade, field, "")
        if value:
            parts.append(qfo_safe_lower(value))
    return " ".join(parts)

def qfo_trade_side(trade):
    return qfo_safe_lower(qfo_trade_get(trade, "side", ""))

def qfo_is_buy_trade(trade):
    return qfo_trade_side(trade) == "buy"

def qfo_is_sell_trade(trade):
    return qfo_trade_side(trade) == "sell"

def qfo_is_stop_loss_label(label):
    label = qfo_safe_lower(label)

    # Explicit stop-loss family. This intentionally catches:
    # - stop_loss
    # - stop_loss_exit
    # - sideways_scalp_stop_loss
    # - quality_initial_stop_loss
    # - initial_stop_loss
    # - trailing_stop_loss
    # - emergency_stop_loss
    if "stop_loss" in label:
        return True

    # Conservative aliases, but avoid false positives from normal risk_off.
    aliases = (
        " stop loss ",
        " stopped out ",
        " stopped_out ",
        " initial sl ",
        " hard_sl ",
        " hard sl ",
    )
    padded = f" {label} "
    return any(alias in padded for alias in aliases)

def qfo_is_take_profit_label(label):
    label = qfo_safe_lower(label)

    if qfo_is_stop_loss_label(label):
        return False

    profit_markers = (
        "take_profit",
        "take profit",
        "adaptive_take_profit",
        "trailing_profit_exit",
        "profit_exit",
        "scalp_take_profit",
        "tp_exit",
    )
    return any(marker in label for marker in profit_markers)

def qfo_is_stop_loss_trade(trade):
    return qfo_is_sell_trade(trade) and qfo_is_stop_loss_label(qfo_trade_label(trade))

def qfo_is_take_profit_trade(trade):
    return qfo_is_sell_trade(trade) and qfo_is_take_profit_label(qfo_trade_label(trade))

def qfo_collect_metric_trades(local_vars=None):
    """
    Pull the best available trade list from local scope or global scope.
    This makes the dashboard/status patch robust across different main.py layouts.
    """
    local_vars = local_vars or {}

    candidate_names = (
        "trades",
        "recent_trades",
        "latest_trades",
        "trade_history",
        "closed_trades",
        "all_trades",
        "TRADE_HISTORY",
        "paper_trades",
    )

    for scope in (local_vars, globals()):
        for name in candidate_names:
            value = scope.get(name) if isinstance(scope, dict) else None
            if isinstance(value, list):
                return value

    # Try common engine objects if present.
    for scope in (local_vars, globals()):
        if not isinstance(scope, dict):
            continue
        for obj_name in ("engine", "bot", "paper_engine", "portfolio", "state", "trader"):
            obj = scope.get(obj_name)
            if obj is None:
                continue
            for attr in candidate_names:
                value = getattr(obj, attr, None)
                if isinstance(value, list):
                    return value

    return []

def qfo_compute_truth_metrics(local_vars=None):
    trades = qfo_collect_metric_trades(local_vars)

    buy_count = 0
    sell_count = 0
    take_profit_count = 0
    stop_loss_count = 0

    for trade in trades:
        if qfo_is_buy_trade(trade):
            buy_count += 1
        elif qfo_is_sell_trade(trade):
            sell_count += 1

        if qfo_is_take_profit_trade(trade):
            take_profit_count += 1

        if qfo_is_stop_loss_trade(trade):
            stop_loss_count += 1

    completed_classified = take_profit_count + stop_loss_count

    if completed_classified > 0:
        win_rate = take_profit_count / completed_classified
    else:
        win_rate = 0.0

    return {
        "total_trades": qfo_metric_value(locals(), "total_trades", buy_count + sell_count),
        "buy_count": qfo_metric_value(locals(), "buy_count", buy_count),
        "sell_count": qfo_metric_value(locals(), "sell_count", sell_count),
        "take_profit_count": qfo_metric_value(locals(), "take_profit_count", take_profit_count),
        "stop_loss_count": qfo_metric_value(locals(), "stop_loss_count", stop_loss_count),
        "win_rate": qfo_metric_value(locals(), "win_rate", win_rate),
        "win_rate_estimate": qfo_metric_value(locals(), "win_rate_estimate", win_rate),
    }

def qfo_metric_value(local_vars, key, fallback=None):
    """
    Return corrected dashboard metric if trade data is available.
    If no trade list is visible in this scope, preserve the old value.
    """
    try:
        trades = qfo_collect_metric_trades(local_vars)
        if not trades:
            return fallback
        metrics = qfo_compute_truth_metrics(local_vars)
        return metrics.get(key, fallback)
    except Exception:
        return fallback

def qfo_repair_metric_payload(payload, local_vars=None):
    """
    Repair a dict payload before it is returned by /status or dashboard code.
    Safe no-op if payload is not a dict or no trades are visible.
    """
    try:
        if not isinstance(payload, dict):
            payload = qfo_repair_metric_payload(payload, locals())
            return payload

        trades = qfo_collect_metric_trades(local_vars)
        if not trades:
            payload = qfo_repair_metric_payload(payload, locals())
            return payload

        metrics = qfo_compute_truth_metrics(local_vars)

        # Repair top-level payload.
        for key, value in metrics.items():
            if key in payload:
                payload[key] = value

        # Repair nested performance payload.
        perf = payload.get("performance")
        if isinstance(perf, dict):
            for key, value in metrics.items():
                if key in perf:
                    perf[key] = value

        # Repair nested metrics payload if used.
        metric_block = payload.get("metrics")
        if isinstance(metric_block, dict):
            for key, value in metrics.items():
                if key in metric_block:
                    metric_block[key] = value

        payload = qfo_repair_metric_payload(payload, locals())
        return payload
    except Exception:
        payload = qfo_repair_metric_payload(payload, locals())
        return payload

# ============================================================
# End METRIC_TRUTH_STOPLOSS_WINRATE_V1
# ============================================================


@app.get('/live-status')
def live_status():
    live = get_live_status_cache()
    if live:
        return live
    return {'name': 'Quant Fund OS', 'status': 'warming_up_or_no_live_cache', 'note': 'Live cache not populated yet. Use dashboard/logs until first loop update.'}



# ============================================================
# QFOS SAFE WRAPPER PATCH — ACTIVE OUTLIER LOSS + BIG LOSER COOLDOWN
# Added as wrappers to avoid fragile inline edits.
# Percentages scale with equity across $10, $50, $100, $500, $1000+ accounts.
# ============================================================

OUTLIER_LOSS_CAP_SIDEWAYS_PCT = globals().get("OUTLIER_LOSS_CAP_SIDEWAYS_PCT", 0.0004)
OUTLIER_LOSS_CAP_TREND_PCT = globals().get("OUTLIER_LOSS_CAP_TREND_PCT", 0.0007)
OUTLIER_LOSS_CAP_RISK_OFF_PCT = globals().get("OUTLIER_LOSS_CAP_RISK_OFF_PCT", 0.0003)
BIG_LOSS_COOLDOWN_HOURS = globals().get("BIG_LOSS_COOLDOWN_HOURS", 6.0)
CATASTROPHIC_LOSS_COOLDOWN_HOURS = globals().get("CATASTROPHIC_LOSS_COOLDOWN_HOURS", 24.0)
CATASTROPHIC_LOSS_MULTIPLIER = globals().get("CATASTROPHIC_LOSS_MULTIPLIER", 2.5)

def _qfos_safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_safe_get(obj, key, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default

def _qfos_safe_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_safe_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_safe_equity(portfolio=None):
    try:
        if portfolio is not None:
            eq = _qfos_safe_float(_qfos_safe_get(portfolio, "equity", None), 0.0)
            if eq > 0:
                return eq
        for name in ("portfolio", "state", "account", "paper_portfolio"):
            obj = globals().get(name)
            eq = _qfos_safe_float(_qfos_safe_get(obj, "equity", None), 0.0)
            if eq > 0:
                return eq
    except Exception:
        pass
    return 100.0

def _qfos_safe_cap_pct(regime):
    r = str(regime or "").upper()
    if r == "RISK_OFF":
        return OUTLIER_LOSS_CAP_RISK_OFF_PCT
    if r == "SIDEWAYS":
        return OUTLIER_LOSS_CAP_SIDEWAYS_PCT
    return OUTLIER_LOSS_CAP_TREND_PCT

def _qfos_safe_ensure_tables():
    try:
        import sqlite3
        conn = sqlite3.connect(_qfos_safe_db_path())
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS symbol_quarantine (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                blocked_until TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS strategy_quarantine (
                strategy TEXT PRIMARY KEY,
                reason TEXT,
                blocked_until TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] ensure_table_error={exc}", flush=True)

def _qfos_safe_write_quarantine(symbol=None, strategy=None, reason="big_loser_cooldown", catastrophic=False):
    try:
        import sqlite3
        from datetime import timedelta

        _qfos_safe_ensure_tables()

        now = _qfos_safe_now_local()
        hours = CATASTROPHIC_LOSS_COOLDOWN_HOURS if catastrophic else BIG_LOSS_COOLDOWN_HOURS
        blocked_until = (now + timedelta(hours=float(hours))).strftime("%Y-%m-%d %H:%M:%S")
        created_at = now.strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(_qfos_safe_db_path())
        cur = conn.cursor()

        if symbol:
            cur.execute(
                "INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at) VALUES (?, ?, ?, ?)",
                (str(symbol), str(reason), blocked_until, created_at),
            )

        if strategy:
            cur.execute(
                "INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at) VALUES (?, ?, ?, ?)",
                (str(strategy), str(reason), blocked_until, created_at),
            )

        conn.commit()
        conn.close()

        print(
            f"[BIG_LOSS_COOLDOWN] blocked symbol={symbol} strategy={strategy} reason={reason} blocked_until={blocked_until}",
            flush=True,
        )
    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] write_error={exc}", flush=True)

def _qfos_safe_symbol_block_reason(symbol):
    if not symbol:
        return None
    try:
        import sqlite3
        from datetime import datetime

        _qfos_safe_ensure_tables()

        conn = sqlite3.connect(_qfos_safe_db_path())
        cur = conn.cursor()
        row = cur.execute(
            "SELECT reason, blocked_until FROM symbol_quarantine WHERE symbol=?",
            (str(symbol),),
        ).fetchone()
        conn.close()

        if not row:
            return None

        reason, blocked_until = row
        try:
            until = datetime.fromisoformat(str(blocked_until))
        except Exception:
            return str(reason or "big_loser_cooldown")

        if until > _qfos_safe_now_local():
            return str(reason or "big_loser_cooldown")

        return None
    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] symbol_read_error symbol={symbol} err={exc}", flush=True)
        return None

def _qfos_safe_strategy_block_reason(strategy):
    if not strategy:
        return None
    try:
        import sqlite3
        from datetime import datetime

        _qfos_safe_ensure_tables()

        conn = sqlite3.connect(_qfos_safe_db_path())
        cur = conn.cursor()
        row = cur.execute(
            "SELECT reason, blocked_until FROM strategy_quarantine WHERE strategy=?",
            (str(strategy),),
        ).fetchone()
        conn.close()

        if not row:
            return None

        reason, blocked_until = row
        try:
            until = datetime.fromisoformat(str(blocked_until))
        except Exception:
            return str(reason or "strategy_big_loser_cooldown")

        if until > _qfos_safe_now_local():
            return str(reason or "strategy_big_loser_cooldown")

        return None
    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] strategy_read_error strategy={strategy} err={exc}", flush=True)
        return None

def _qfos_safe_order_block_reason(order):
    try:
        side = str(_qfos_safe_get(order, "side", "") or "").lower()
        if side and side != "buy":
            return None

        symbol = _qfos_safe_get(order, "symbol", None)
        strategy = _qfos_safe_get(order, "strategy", None)

        sr = _qfos_safe_symbol_block_reason(symbol)
        if sr:
            return f"symbol_quarantine:{sr}"

        tr = _qfos_safe_strategy_block_reason(strategy)
        if tr:
            return f"strategy_quarantine:{tr}"

        return None
    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] order_check_error={exc}", flush=True)
        return None

def _qfos_safe_position_loss_usd(position, mark_price):
    qty = abs(_qfos_safe_float(_qfos_safe_get(position, "quantity", _qfos_safe_get(position, "qty", 0.0)), 0.0))
    entry = _qfos_safe_float(_qfos_safe_get(position, "avg_entry", _qfos_safe_get(position, "entry_price", 0.0)), 0.0)
    mark = _qfos_safe_float(mark_price, 0.0)

    if qty <= 0 or entry <= 0 or mark <= 0:
        return 0.0

    return max((entry - mark) * qty, 0.0)

def _qfos_safe_find_position_and_price(bound_args):
    position = None
    mark_price = None
    regime = "SIDEWAYS"
    portfolio = None

    for k, v in bound_args.items():
        lk = str(k).lower()

        if lk in ("position", "pos", "p"):
            position = v
        elif lk in ("mark_price", "price", "current_price", "last_price"):
            mark_price = v
        elif lk == "regime":
            regime = v
        elif lk == "portfolio":
            portfolio = v

    if position is None:
        for v in bound_args.values():
            if isinstance(v, dict):
                if ("quantity" in v or "qty" in v) and ("avg_entry" in v or "entry_price" in v):
                    position = v
                    break
            else:
                if hasattr(v, "quantity") and (hasattr(v, "avg_entry") or hasattr(v, "entry_price")):
                    position = v
                    break

    if mark_price is None:
        for k, v in bound_args.items():
            lk = str(k).lower()
            if "price" in lk and isinstance(v, (int, float)):
                mark_price = v
                break

    return position, mark_price, regime, portfolio

def _qfos_safe_outlier_reason(position, mark_price, regime, portfolio=None):
    try:
        equity = _qfos_safe_equity(portfolio)
        cap_pct = _qfos_safe_cap_pct(regime)
        cap_usd = max(equity, 1.0) * cap_pct
        loss_usd = _qfos_safe_position_loss_usd(position, mark_price)

        if loss_usd <= 0:
            return None

        symbol = _qfos_safe_get(position, "symbol", None)
        strategy = _qfos_safe_get(position, "strategy", None)
        catastrophic_cap = cap_usd * CATASTROPHIC_LOSS_MULTIPLIER

        if loss_usd >= cap_usd:
            catastrophic = loss_usd >= catastrophic_cap
            reason = "catastrophic_outlier_loss_cap" if catastrophic else "outlier_loss_cap"

            print(
                f"[OUTLIER_LOSS_CAP_ACTIVE] exit symbol={symbol} strategy={strategy} reason={reason} "
                f"loss_usd={loss_usd:.6f} equity={equity:.2f} "
                f"cap_usd={cap_usd:.6f} cap_pct={cap_pct:.6%} regime={regime}",
                flush=True,
            )

            _qfos_safe_write_quarantine(
                symbol=symbol,
                strategy=strategy,
                reason=reason,
                catastrophic=catastrophic,
            )

            return reason

        return None
    except Exception as exc:
        print(f"[OUTLIER_LOSS_CAP_ACTIVE] reason_error={exc}", flush=True)
        return None

def _qfos_safe_install_wrappers():
    try:
        import inspect

        g = globals()

        if "_qfos_exit_decision" in g and not getattr(g["_qfos_exit_decision"], "_qfos_safe_wrapped", False):
            _orig_exit = g["_qfos_exit_decision"]

            def _wrapped_exit(*args, **kwargs):
                try:
                    bound = {}
                    try:
                        sig = inspect.signature(_orig_exit)
                        bound = dict(sig.bind_partial(*args, **kwargs).arguments)
                    except Exception:
                        bound = {f"arg{i}": v for i, v in enumerate(args)}
                        bound.update(kwargs)

                    position, mark_price, regime, portfolio = _qfos_safe_find_position_and_price(bound)

                    if position is not None and mark_price is not None:
                        reason = _qfos_safe_outlier_reason(position, mark_price, regime, portfolio)
                        if reason:
                            return reason
                except Exception as exc:
                    print(f"[OUTLIER_LOSS_CAP_ACTIVE] wrapper_error={exc}", flush=True)

                return _orig_exit(*args, **kwargs)

            _wrapped_exit._qfos_safe_wrapped = True
            g["_qfos_exit_decision"] = _wrapped_exit
            print("[BIG_LOSS_COOLDOWN] wrapped _qfos_exit_decision", flush=True)

        if "_entry_quality_reason" in g and not getattr(g["_entry_quality_reason"], "_qfos_safe_wrapped", False):
            _orig_entry = g["_entry_quality_reason"]

            def _wrapped_entry(*args, **kwargs):
                try:
                    symbol = kwargs.get("symbol", None)
                    strategy = kwargs.get("strategy", None)

                    for a in args:
                        if symbol is None and isinstance(a, str) and "/" in a:
                            symbol = a
                        if isinstance(a, dict):
                            symbol = symbol or a.get("symbol")
                            strategy = strategy or a.get("strategy")

                    sr = _qfos_safe_symbol_block_reason(symbol)
                    if sr:
                        print(f"[BIG_LOSS_COOLDOWN] entry rejected symbol={symbol} reason={sr}", flush=True)
                        return "big_loser_cooldown"

                    tr = _qfos_safe_strategy_block_reason(strategy)
                    if tr:
                        print(f"[BIG_LOSS_COOLDOWN] entry rejected strategy={strategy} reason={tr}", flush=True)
                        return "strategy_big_loser_cooldown"
                except Exception as exc:
                    print(f"[BIG_LOSS_COOLDOWN] entry_wrapper_error={exc}", flush=True)

                return _orig_entry(*args, **kwargs)

            _wrapped_entry._qfos_safe_wrapped = True
            g["_entry_quality_reason"] = _wrapped_entry
            print("[BIG_LOSS_COOLDOWN] wrapped _entry_quality_reason", flush=True)

        if "_filter_and_resize_orders" in g and not getattr(g["_filter_and_resize_orders"], "_qfos_safe_wrapped", False):
            _orig_filter = g["_filter_and_resize_orders"]

            def _wrapped_filter(*args, **kwargs):
                result = _orig_filter(*args, **kwargs)

                try:
                    def clean_list(items):
                        if not isinstance(items, list):
                            return items
                        kept = []
                        for order in items:
                            br = _qfos_safe_order_block_reason(order)
                            if br:
                                print(
                                    f"[BIG_LOSS_COOLDOWN] order blocked symbol={_qfos_safe_get(order, 'symbol', None)} "
                                    f"strategy={_qfos_safe_get(order, 'strategy', None)} reason={br}",
                                    flush=True,
                                )
                                continue
                            kept.append(order)
                        return kept

                    if isinstance(result, list):
                        return clean_list(result)

                    for obj in list(args) + list(kwargs.values()):
                        if isinstance(obj, list):
                            obj[:] = clean_list(obj)

                    result = qfo_repair_metric_payload(result, locals())
                    return result
                except Exception as exc:
                    print(f"[BIG_LOSS_COOLDOWN] filter_wrapper_error={exc}", flush=True)
                    result = qfo_repair_metric_payload(result, locals())
                    return result

            _wrapped_filter._qfos_safe_wrapped = True
            g["_filter_and_resize_orders"] = _wrapped_filter
            print("[BIG_LOSS_COOLDOWN] wrapped _filter_and_resize_orders", flush=True)

    except Exception as exc:
        print(f"[BIG_LOSS_COOLDOWN] install_error={exc}", flush=True)

_qfos_safe_install_wrappers()




# ============================================================
# QFOS BASKET LOSS GUARD — ACTIVE WRAPPER
# Purpose:
#   Prevent several small losing positions from combining into
#   one large portfolio-level drawdown.
#
# Percentages are fractions of current equity:
#   0.0010 = 0.10% of equity
#   0.0018 = 0.18% of equity
# ============================================================

BASKET_LOSS_CAP_SIDEWAYS_PCT = globals().get("BASKET_LOSS_CAP_SIDEWAYS_PCT", 0.0010)
BASKET_LOSS_CAP_TREND_PCT = globals().get("BASKET_LOSS_CAP_TREND_PCT", 0.0018)
BASKET_LOSS_CAP_RISK_OFF_PCT = globals().get("BASKET_LOSS_CAP_RISK_OFF_PCT", 0.0008)
BASKET_MIN_POSITION_LOSS_PCT = globals().get("BASKET_MIN_POSITION_LOSS_PCT", 0.00015)

def _qfos_basket_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_basket_get(obj, key, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default

def _qfos_basket_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_basket_equity(portfolio=None):
    try:
        if portfolio is not None:
            eq = _qfos_basket_float(_qfos_basket_get(portfolio, "equity", None), 0.0)
            if eq > 0:
                return eq

        for name in ("portfolio", "state", "account", "paper_portfolio"):
            obj = globals().get(name)
            eq = _qfos_basket_float(_qfos_basket_get(obj, "equity", None), 0.0)
            if eq > 0:
                return eq
    except Exception:
        pass
    return 100.0

def _qfos_basket_cap_pct(regime):
    r = str(regime or "").upper()
    if r == "RISK_OFF":
        return BASKET_LOSS_CAP_RISK_OFF_PCT
    if r == "SIDEWAYS":
        return BASKET_LOSS_CAP_SIDEWAYS_PCT
    return BASKET_LOSS_CAP_TREND_PCT

def _qfos_basket_open_loss_usd():
    try:
        import sqlite3
        conn = sqlite3.connect(_qfos_basket_db_path())
        cur = conn.cursor()
        row = cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN unrealized_pnl < 0 THEN -unrealized_pnl ELSE 0 END), 0)
            FROM positions
            WHERE quantity > 0
        """).fetchone()
        conn.close()
        return _qfos_basket_float(row[0] if row else 0.0, 0.0)
    except Exception as exc:
        print(f"[BASKET_LOSS_GUARD] db_loss_error={exc}", flush=True)
        return 0.0

def _qfos_basket_position_loss_usd(position, mark_price):
    qty = abs(_qfos_basket_float(
        _qfos_basket_get(position, "quantity", _qfos_basket_get(position, "qty", 0.0)),
        0.0
    ))
    entry = _qfos_basket_float(
        _qfos_basket_get(position, "avg_entry", _qfos_basket_get(position, "entry_price", 0.0)),
        0.0
    )
    mark = _qfos_basket_float(mark_price, 0.0)

    if qty <= 0 or entry <= 0 or mark <= 0:
        return 0.0

    return max((entry - mark) * qty, 0.0)

def _qfos_basket_find_position_and_price(bound_args):
    position = None
    mark_price = None
    regime = "SIDEWAYS"
    portfolio = None

    for k, v in bound_args.items():
        lk = str(k).lower()
        if lk in ("position", "pos", "p"):
            position = v
        elif lk in ("mark_price", "price", "current_price", "last_price"):
            mark_price = v
        elif lk == "regime":
            regime = v
        elif lk == "portfolio":
            portfolio = v

    if position is None:
        for v in bound_args.values():
            if isinstance(v, dict):
                if ("quantity" in v or "qty" in v) and ("avg_entry" in v or "entry_price" in v):
                    position = v
                    break
            else:
                if hasattr(v, "quantity") and (hasattr(v, "avg_entry") or hasattr(v, "entry_price")):
                    position = v
                    break

    if mark_price is None:
        for k, v in bound_args.items():
            if "price" in str(k).lower() and isinstance(v, (int, float)):
                mark_price = v
                break

    return position, mark_price, regime, portfolio

def _qfos_basket_exit_reason(position, mark_price, regime, portfolio=None):
    try:
        equity = _qfos_basket_equity(portfolio)
        basket_loss = _qfos_basket_open_loss_usd()
        basket_cap = max(equity, 1.0) * _qfos_basket_cap_pct(regime)
        position_loss = _qfos_basket_position_loss_usd(position, mark_price)
        min_position_loss = max(equity, 1.0) * BASKET_MIN_POSITION_LOSS_PCT

        if basket_loss >= basket_cap and position_loss >= min_position_loss:
            symbol = _qfos_basket_get(position, "symbol", None)
            strategy = _qfos_basket_get(position, "strategy", None)

            print(
                f"[BASKET_LOSS_GUARD] exit symbol={symbol} strategy={strategy} "
                f"position_loss={position_loss:.6f} basket_loss={basket_loss:.6f} "
                f"basket_cap={basket_cap:.6f} equity={equity:.2f} regime={regime}",
                flush=True,
            )

            return "basket_loss_cap"

        return None
    except Exception as exc:
        print(f"[BASKET_LOSS_GUARD] reason_error={exc}", flush=True)
        return None

def _qfos_basket_install_wrapper():
    try:
        import inspect
        g = globals()

        if "_qfos_exit_decision" in g and not getattr(g["_qfos_exit_decision"], "_qfos_basket_wrapped", False):
            _orig_exit = g["_qfos_exit_decision"]

            def _wrapped_basket_exit(*args, **kwargs):
                try:
                    bound = {}
                    try:
                        sig = inspect.signature(_orig_exit)
                        bound = dict(sig.bind_partial(*args, **kwargs).arguments)
                    except Exception:
                        bound = {f"arg{i}": v for i, v in enumerate(args)}
                        bound.update(kwargs)

                    position, mark_price, regime, portfolio = _qfos_basket_find_position_and_price(bound)

                    if position is not None and mark_price is not None:
                        reason = _qfos_basket_exit_reason(position, mark_price, regime, portfolio)
                        if reason:
                            return reason
                except Exception as exc:
                    print(f"[BASKET_LOSS_GUARD] wrapper_error={exc}", flush=True)

                return _orig_exit(*args, **kwargs)

            _wrapped_basket_exit._qfos_basket_wrapped = True
            g["_qfos_exit_decision"] = _wrapped_basket_exit
            print("[BASKET_LOSS_GUARD] wrapped _qfos_exit_decision", flush=True)
    except Exception as exc:
        print(f"[BASKET_LOSS_GUARD] install_error={exc}", flush=True)

_qfos_basket_install_wrapper()




# ============================================================
# QFOS EMERGENCY BASKET WATCHDOG — DB-LEVEL PAPER EXIT
# Purpose:
#   The previous wrappers load correctly, but logs show exits=0
#   when no proposed exits/orders are created. This watchdog does
#   not depend on _qfos_exit_decision. It directly protects paper
#   equity by closing the worst losing open position when total
#   open basket loss breaches an equity-scaled cap.
# ============================================================

QFOS_WATCHDOG_ENABLED = globals().get("QFOS_WATCHDOG_ENABLED", True)
QFOS_WATCHDOG_INTERVAL_SECONDS = globals().get("QFOS_WATCHDOG_INTERVAL_SECONDS", 8.0)

# 0.10% basket cap in SIDEWAYS. At $100 = about $0.10. At $1000 = about $1.00.
QFOS_WATCHDOG_BASKET_SIDEWAYS_PCT = globals().get("QFOS_WATCHDOG_BASKET_SIDEWAYS_PCT", 0.0010)
QFOS_WATCHDOG_BASKET_TREND_PCT = globals().get("QFOS_WATCHDOG_BASKET_TREND_PCT", 0.0018)
QFOS_WATCHDOG_BASKET_RISK_OFF_PCT = globals().get("QFOS_WATCHDOG_BASKET_RISK_OFF_PCT", 0.0008)

# Do not close tiny noise positions unless individual loss is at least 0.015% equity.
QFOS_WATCHDOG_MIN_POSITION_LOSS_PCT = globals().get("QFOS_WATCHDOG_MIN_POSITION_LOSS_PCT", 0.00015)

QFOS_WATCHDOG_SYMBOL_COOLDOWN_HOURS = globals().get("QFOS_WATCHDOG_SYMBOL_COOLDOWN_HOURS", 6.0)
QFOS_WATCHDOG_STRATEGY_COOLDOWN_HOURS = globals().get("QFOS_WATCHDOG_STRATEGY_COOLDOWN_HOURS", 6.0)

def _qfos_watchdog_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_watchdog_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_watchdog_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_watchdog_ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_quarantine (
            symbol TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS strategy_quarantine (
            strategy TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)

def _qfos_watchdog_latest_equity_and_regime(cur):
    try:
        row = cur.execute("""
            SELECT equity, regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        if row:
            equity = _qfos_watchdog_float(row[0], 100.0)
            regime = str(row[1] or "SIDEWAYS")
            if equity > 0:
                return equity, regime
    except Exception:
        pass
    return 100.0, "SIDEWAYS"

def _qfos_watchdog_basket_cap_pct(regime):
    r = str(regime or "").upper()
    if r == "RISK_OFF":
        return QFOS_WATCHDOG_BASKET_RISK_OFF_PCT
    if r == "SIDEWAYS":
        return QFOS_WATCHDOG_BASKET_SIDEWAYS_PCT
    return QFOS_WATCHDOG_BASKET_TREND_PCT

def _qfos_watchdog_close_worst_loser_once():
    import sqlite3
    from datetime import timedelta

    db = _qfos_watchdog_db_path()
    conn = sqlite3.connect(db, timeout=10)
    cur = conn.cursor()

    try:
        _qfos_watchdog_ensure_tables(cur)

        equity, regime = _qfos_watchdog_latest_equity_and_regime(cur)
        basket_cap = max(equity, 1.0) * _qfos_watchdog_basket_cap_pct(regime)
        min_position_loss = max(equity, 1.0) * QFOS_WATCHDOG_MIN_POSITION_LOSS_PCT

        rows = cur.execute("""
            SELECT symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, strategy
            FROM positions
            WHERE quantity > 0
        """).fetchall()

        if not rows:
            conn.close()
            return

        losing = []
        for symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, strategy in rows:
            qty = abs(_qfos_watchdog_float(quantity))
            entry = _qfos_watchdog_float(avg_entry)
            mark = _qfos_watchdog_float(last_price)
            db_unreal = _qfos_watchdog_float(unrealized_pnl)

            if qty <= 0 or entry <= 0 or mark <= 0:
                continue

            calc_loss = max((entry - mark) * qty, 0.0)
            db_loss = max(-db_unreal, 0.0)
            loss = max(calc_loss, db_loss)

            if loss > 0:
                losing.append({
                    "symbol": symbol,
                    "quantity": qty,
                    "avg_entry": entry,
                    "last_price": mark,
                    "exposure": _qfos_watchdog_float(exposure),
                    "unrealized_pnl": db_unreal,
                    "strategy": strategy,
                    "loss": loss,
                })

        basket_loss = sum(x["loss"] for x in losing)

        if basket_loss < basket_cap:
            conn.close()
            return

        losing = [x for x in losing if x["loss"] >= min_position_loss]
        if not losing:
            conn.close()
            return

        worst = sorted(losing, key=lambda x: x["loss"], reverse=True)[0]

        symbol = str(worst["symbol"])
        strategy = str(worst["strategy"] or "unknown_strategy")
        qty = worst["quantity"]
        mark = worst["last_price"]
        loss = worst["loss"]
        pnl = -abs(loss)

        now = _qfos_watchdog_now_local()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")
        blocked_until = (now + timedelta(hours=float(QFOS_WATCHDOG_SYMBOL_COOLDOWN_HOURS))).strftime("%Y-%m-%d %H:%M:%S")

        print(
            "[EMERGENCY_BASKET_WATCHDOG] closing worst loser "
            f"symbol={symbol} strategy={strategy} qty={qty:.8f} mark={mark:.8f} "
            f"position_loss={loss:.6f} basket_loss={basket_loss:.6f} "
            f"basket_cap={basket_cap:.6f} equity={equity:.2f} regime={regime}",
            flush=True,
        )

        # Record paper sell.
        cur.execute("""
            INSERT INTO trades (
                symbol, side, quantity, expected_price, fill_price,
                slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
            )
            VALUES (?, 'sell', ?, ?, ?, 0.0, ?, 'basket_loss_cap', 1.0, 0, 0, ?)
        """, (symbol, qty, mark, mark, pnl, now_s))

        # Flatten position in paper DB.
        cur.execute("""
            UPDATE positions
            SET quantity = 0.0,
                avg_entry = 0.0,
                realized_pnl = COALESCE(realized_pnl, 0.0) + ?,
                unrealized_pnl = 0.0,
                exposure = 0.0,
                strategy = 'basket_loss_cap',
                updated_at = ?
            WHERE symbol = ?
        """, (pnl, now_s, symbol))

        # Quarantine symbol and source strategy.
        cur.execute("""
            INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at)
            VALUES (?, 'basket_loss_cap', ?, ?)
        """, (symbol, blocked_until, now_s))

        if strategy and strategy != "unknown_strategy":
            cur.execute("""
                INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at)
                VALUES (?, 'basket_loss_cap', ?, ?)
            """, (strategy, blocked_until, now_s))

        conn.commit()

        print(
            "[EMERGENCY_BASKET_WATCHDOG] closed "
            f"symbol={symbol} pnl={pnl:.6f} blocked_until={blocked_until}",
            flush=True,
        )

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[EMERGENCY_BASKET_WATCHDOG] error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_watchdog_loop():
    import time
    while True:
        try:
            if QFOS_WATCHDOG_ENABLED:
                _qfos_watchdog_close_worst_loser_once()
        except Exception as exc:
            print(f"[EMERGENCY_BASKET_WATCHDOG] loop_error={exc}", flush=True)
        time.sleep(float(QFOS_WATCHDOG_INTERVAL_SECONDS))

def _qfos_start_emergency_basket_watchdog():
    try:
        import threading
        if globals().get("_QFOS_EMERGENCY_BASKET_WATCHDOG_STARTED"):
            return
        globals()["_QFOS_EMERGENCY_BASKET_WATCHDOG_STARTED"] = True
        t = threading.Thread(target=_qfos_watchdog_loop, daemon=True, name="qfos_emergency_basket_watchdog")
        t.start()
        print("[EMERGENCY_BASKET_WATCHDOG] started", flush=True)
    except Exception as exc:
        print(f"[EMERGENCY_BASKET_WATCHDOG] start_error={exc}", flush=True)

_qfos_start_emergency_basket_watchdog()




# ============================================================
# QFOS FALLBACK SCOUT QUALITY GUARD
# Purpose:
#   Prevent fallback scout from proposing trades when the quality
#   rank is empty, unless the candidate is a very strong breakout
#   with positive one-tick momentum.
#
# This does NOT reduce normal ranked/evo entries.
# It only restricts fallback_scout_breakout.
# ============================================================

QFOS_FALLBACK_GUARD_ENABLED = globals().get("QFOS_FALLBACK_GUARD_ENABLED", True)

# "Very strong breakout" requirements.
QFOS_FALLBACK_MIN_SIGNAL_STRENGTH = globals().get("QFOS_FALLBACK_MIN_SIGNAL_STRENGTH", 0.0120)
QFOS_FALLBACK_MIN_BREAKOUT_SCORE = globals().get("QFOS_FALLBACK_MIN_BREAKOUT_SCORE", 0.0100)
QFOS_FALLBACK_MIN_TREND_QUALITY = globals().get("QFOS_FALLBACK_MIN_TREND_QUALITY", 0.0100)
QFOS_FALLBACK_MIN_ONE_TICK_MOMENTUM = globals().get("QFOS_FALLBACK_MIN_ONE_TICK_MOMENTUM", 0.00020)
QFOS_FALLBACK_MIN_MOMENTUM = globals().get("QFOS_FALLBACK_MIN_MOMENTUM", 0.00150)

def _qfos_fb_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_fb_get(obj, key, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default

def _qfos_fb_feature_from_order(order):
    feature = _qfos_fb_get(order, "feature", None)
    if isinstance(feature, dict):
        return feature
    return {}

def _qfos_fb_is_fallback_order(order):
    strategy = str(_qfos_fb_get(order, "strategy", "") or "")
    return strategy == "fallback_scout_breakout" or strategy.startswith("fallback_scout")

def _qfos_fb_quality_rank_empty(local_vars=None):
    """
    Best-effort detection of empty quality rank.
    We check common local variable names used around quality ranking.
    If we cannot find any populated quality list, we treat it as empty
    for fallback scout safety.
    """
    try:
        local_vars = local_vars or {}

        names = [
            "entry_quality_top",
            "entry_quality_top_10",
            "entry_quality_top_symbols",
            "quality_top",
            "quality_rank",
            "quality_ranked",
            "ranked_quality",
            "top_quality",
            "top_quality_symbols",
            "quality_candidates",
            "quality_symbols",
        ]

        for name in names:
            value = local_vars.get(name, None)
            if isinstance(value, (list, tuple, set)) and len(value) > 0:
                return False
            if isinstance(value, dict) and len(value) > 0:
                return False

        # Some code stores this globally/status-style.
        for name in names:
            value = globals().get(name, None)
            if isinstance(value, (list, tuple, set)) and len(value) > 0:
                return False
            if isinstance(value, dict) and len(value) > 0:
                return False

        return True
    except Exception:
        return True

def _qfos_fb_is_very_strong_breakout(order):
    feature = _qfos_fb_feature_from_order(order)

    signal_strength = _qfos_fb_float(
        _qfos_fb_get(order, "signal_strength", _qfos_fb_get(feature, "signal_strength", 0.0)),
        0.0
    )
    breakout_score = _qfos_fb_float(_qfos_fb_get(feature, "breakout_score", 0.0), 0.0)
    trend_quality = _qfos_fb_float(_qfos_fb_get(feature, "trend_quality", 0.0), 0.0)
    one_tick = _qfos_fb_float(_qfos_fb_get(feature, "one_tick_momentum", 0.0), 0.0)
    momentum = _qfos_fb_float(_qfos_fb_get(feature, "momentum", 0.0), 0.0)

    regime = str(_qfos_fb_get(feature, "symbol_regime", "") or "")
    is_uptrend = bool(_qfos_fb_get(feature, "is_symbol_uptrend", False))

    return (
        signal_strength >= QFOS_FALLBACK_MIN_SIGNAL_STRENGTH
        and breakout_score >= QFOS_FALLBACK_MIN_BREAKOUT_SCORE
        and trend_quality >= QFOS_FALLBACK_MIN_TREND_QUALITY
        and one_tick >= QFOS_FALLBACK_MIN_ONE_TICK_MOMENTUM
        and momentum >= QFOS_FALLBACK_MIN_MOMENTUM
        and is_uptrend
        and regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
    )

def _qfos_fb_filter_orders(order_list, local_vars=None, source="unknown"):
    if not QFOS_FALLBACK_GUARD_ENABLED:
        return order_list

    if not isinstance(order_list, list):
        return order_list

    quality_empty = _qfos_fb_quality_rank_empty(local_vars)

    kept = []
    for order in order_list:
        try:
            if not _qfos_fb_is_fallback_order(order):
                kept.append(order)
                continue

            if not quality_empty:
                kept.append(order)
                continue

            if _qfos_fb_is_very_strong_breakout(order):
                kept.append(order)
                print(
                    "[FALLBACK_QUALITY_GUARD] allowed strong fallback "
                    f"symbol={_qfos_fb_get(order, 'symbol', None)} "
                    f"source={source}",
                    flush=True,
                )
                continue

            feature = _qfos_fb_feature_from_order(order)
            print(
                "[FALLBACK_QUALITY_GUARD] blocked fallback because quality_rank_empty "
                f"symbol={_qfos_fb_get(order, 'symbol', None)} "
                f"signal={_qfos_fb_float(_qfos_fb_get(order, 'signal_strength', _qfos_fb_get(feature, 'signal_strength', 0.0))):.6f} "
                f"breakout={_qfos_fb_float(_qfos_fb_get(feature, 'breakout_score', 0.0)):.6f} "
                f"trend_quality={_qfos_fb_float(_qfos_fb_get(feature, 'trend_quality', 0.0)):.6f} "
                f"one_tick={_qfos_fb_float(_qfos_fb_get(feature, 'one_tick_momentum', 0.0)):.6f} "
                f"momentum={_qfos_fb_float(_qfos_fb_get(feature, 'momentum', 0.0)):.6f} "
                f"regime={_qfos_fb_get(feature, 'symbol_regime', None)} "
                f"source={source}",
                flush=True,
            )
        except Exception as exc:
            print(f"[FALLBACK_QUALITY_GUARD] error={exc}", flush=True)
            kept.append(order)

    return kept

def _qfos_fb_install_wrappers():
    """
    Wrap common order filtering functions if they exist.
    This is safe: if a function name does not exist, nothing breaks.
    """
    try:
        g = globals()

        candidate_names = [
            "_filter_and_resize_orders",
            "filter_and_resize_orders",
            "_apply_expectancy_patch",
            "apply_expectancy_patch",
            "_qfos_expectancy_filter_orders",
            "qfos_expectancy_filter_orders",
        ]

        for name in candidate_names:
            fn = g.get(name)
            if fn is None or not callable(fn):
                continue
            if getattr(fn, "_qfos_fb_wrapped", False):
                continue

            def make_wrapper(original, func_name):
                def wrapped(*args, **kwargs):
                    result = original(*args, **kwargs)

                    try:
                        # If function returns order list, filter returned list.
                        if isinstance(result, list):
                            return _qfos_fb_filter_orders(
                                result,
                                local_vars={},
                                source=f"return:{func_name}",
                            )

                        # If function mutates a list argument, filter list args in place.
                        for arg in args:
                            if isinstance(arg, list):
                                arg[:] = _qfos_fb_filter_orders(
                                    arg,
                                    local_vars={},
                                    source=f"arg:{func_name}",
                                )

                        for key, value in kwargs.items():
                            if isinstance(value, list):
                                value[:] = _qfos_fb_filter_orders(
                                    value,
                                    local_vars={},
                                    source=f"kwarg:{func_name}:{key}",
                                )

                        result = qfo_repair_metric_payload(result, locals())
                        return result
                    except Exception as exc:
                        print(f"[FALLBACK_QUALITY_GUARD] wrapper_error func={func_name} err={exc}", flush=True)
                        result = qfo_repair_metric_payload(result, locals())
                        return result

                wrapped._qfos_fb_wrapped = True
                return wrapped

            g[name] = make_wrapper(fn, name)
            print(f"[FALLBACK_QUALITY_GUARD] wrapped {name}", flush=True)

    except Exception as exc:
        print(f"[FALLBACK_QUALITY_GUARD] install_error={exc}", flush=True)

_qfos_fb_install_wrappers()




# ============================================================
# QFOS FALLBACK SCOUT SOURCE GUARD
# Purpose:
#   Block weak fallback_scout_breakout candidates immediately
#   after scout injection, before ORDERS / EXPECTANCY_PATCH.
#
# This is stricter than the wrapper guard because it protects
# the source injection point directly.
# ============================================================

QFOS_FALLBACK_SOURCE_GUARD_ENABLED = globals().get("QFOS_FALLBACK_SOURCE_GUARD_ENABLED", True)

QFOS_FALLBACK_SOURCE_MIN_SIGNAL_STRENGTH = globals().get("QFOS_FALLBACK_SOURCE_MIN_SIGNAL_STRENGTH", 0.0120)
QFOS_FALLBACK_SOURCE_MIN_BREAKOUT_SCORE = globals().get("QFOS_FALLBACK_SOURCE_MIN_BREAKOUT_SCORE", 0.0100)
QFOS_FALLBACK_SOURCE_MIN_TREND_QUALITY = globals().get("QFOS_FALLBACK_SOURCE_MIN_TREND_QUALITY", 0.0100)
QFOS_FALLBACK_SOURCE_MIN_ONE_TICK_MOMENTUM = globals().get("QFOS_FALLBACK_SOURCE_MIN_ONE_TICK_MOMENTUM", 0.00020)
QFOS_FALLBACK_SOURCE_MIN_MOMENTUM = globals().get("QFOS_FALLBACK_SOURCE_MIN_MOMENTUM", 0.00150)

# Avoid tiny / noisy symbols unless the breakout is exceptionally clean.
QFOS_FALLBACK_SOURCE_MIN_PRICE = globals().get("QFOS_FALLBACK_SOURCE_MIN_PRICE", 0.01)
QFOS_FALLBACK_SOURCE_ALLOW_LOW_PRICE_IF_SIGNAL = globals().get("QFOS_FALLBACK_SOURCE_ALLOW_LOW_PRICE_IF_SIGNAL", 0.0300)

def _qfos_fbs_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_fbs_get(obj, key, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default

def _qfos_fbs_feature(order):
    feature = _qfos_fbs_get(order, "feature", None)
    return feature if isinstance(feature, dict) else {}

def _qfos_fbs_is_fallback(order):
    strategy = str(_qfos_fbs_get(order, "strategy", "") or "")
    return strategy == "fallback_scout_breakout" or strategy.startswith("fallback_scout")

def _qfos_fbs_quality_rank_empty(local_vars=None):
    try:
        local_vars = local_vars or {}
        names = [
            "entry_quality_top",
            "entry_quality_top_10",
            "entry_quality_top_symbols",
            "quality_top",
            "quality_rank",
            "quality_ranked",
            "ranked_quality",
            "top_quality",
            "top_quality_symbols",
            "quality_candidates",
            "quality_symbols",
        ]

        for name in names:
            value = local_vars.get(name, None)
            if isinstance(value, (list, tuple, set)) and len(value) > 0:
                return False
            if isinstance(value, dict) and len(value) > 0:
                return False

        for name in names:
            value = globals().get(name, None)
            if isinstance(value, (list, tuple, set)) and len(value) > 0:
                return False
            if isinstance(value, dict) and len(value) > 0:
                return False

        return True
    except Exception:
        return True

def _qfos_fbs_is_strong_enough(order):
    feature = _qfos_fbs_feature(order)

    symbol = _qfos_fbs_get(order, "symbol", "UNKNOWN")
    signal = _qfos_fbs_float(_qfos_fbs_get(order, "signal_strength", _qfos_fbs_get(feature, "signal_strength", 0.0)), 0.0)
    breakout = _qfos_fbs_float(_qfos_fbs_get(feature, "breakout_score", 0.0), 0.0)
    trend_quality = _qfos_fbs_float(_qfos_fbs_get(feature, "trend_quality", 0.0), 0.0)
    one_tick = _qfos_fbs_float(_qfos_fbs_get(feature, "one_tick_momentum", 0.0), 0.0)
    momentum = _qfos_fbs_float(_qfos_fbs_get(feature, "momentum", 0.0), 0.0)
    price = _qfos_fbs_float(_qfos_fbs_get(feature, "price", _qfos_fbs_get(order, "fill_price", 0.0)), 0.0)

    regime = str(_qfos_fbs_get(feature, "symbol_regime", "") or "")
    is_uptrend = bool(_qfos_fbs_get(feature, "is_symbol_uptrend", False))
    is_choppy = bool(_qfos_fbs_get(feature, "is_choppy", False))

    price_ok = price >= QFOS_FALLBACK_SOURCE_MIN_PRICE or signal >= QFOS_FALLBACK_SOURCE_ALLOW_LOW_PRICE_IF_SIGNAL

    ok = (
        signal >= QFOS_FALLBACK_SOURCE_MIN_SIGNAL_STRENGTH
        and breakout >= QFOS_FALLBACK_SOURCE_MIN_BREAKOUT_SCORE
        and trend_quality >= QFOS_FALLBACK_SOURCE_MIN_TREND_QUALITY
        and one_tick >= QFOS_FALLBACK_SOURCE_MIN_ONE_TICK_MOMENTUM
        and momentum >= QFOS_FALLBACK_SOURCE_MIN_MOMENTUM
        and is_uptrend
        and not is_choppy
        and regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
        and price_ok
    )

    if not ok:
        print(
            "[FALLBACK_SOURCE_GUARD] blocked weak fallback "
            f"symbol={symbol} "
            f"signal={signal:.6f}/{QFOS_FALLBACK_SOURCE_MIN_SIGNAL_STRENGTH:.6f} "
            f"breakout={breakout:.6f}/{QFOS_FALLBACK_SOURCE_MIN_BREAKOUT_SCORE:.6f} "
            f"trend_quality={trend_quality:.6f}/{QFOS_FALLBACK_SOURCE_MIN_TREND_QUALITY:.6f} "
            f"one_tick={one_tick:.6f}/{QFOS_FALLBACK_SOURCE_MIN_ONE_TICK_MOMENTUM:.6f} "
            f"momentum={momentum:.6f}/{QFOS_FALLBACK_SOURCE_MIN_MOMENTUM:.6f} "
            f"price={price:.8f} "
            f"regime={regime} "
            f"is_uptrend={is_uptrend} "
            f"is_choppy={is_choppy}",
            flush=True,
        )
    else:
        print(
            "[FALLBACK_SOURCE_GUARD] allowed strong fallback "
            f"symbol={symbol} "
            f"signal={signal:.6f} breakout={breakout:.6f} "
            f"trend_quality={trend_quality:.6f} one_tick={one_tick:.6f} "
            f"momentum={momentum:.6f} regime={regime}",
            flush=True,
        )

    return ok

def _qfos_fbs_filter_proposed_fills(order_list, local_vars=None, source="source"):
    if not QFOS_FALLBACK_SOURCE_GUARD_ENABLED:
        return order_list

    if not isinstance(order_list, list):
        return order_list

    quality_empty = _qfos_fbs_quality_rank_empty(local_vars)

    kept = []
    for order in order_list:
        try:
            if not _qfos_fbs_is_fallback(order):
                kept.append(order)
                continue

            # We only restrict fallback when the quality ladder is empty.
            if not quality_empty:
                kept.append(order)
                continue

            if _qfos_fbs_is_strong_enough(order):
                kept.append(order)
                continue

            # blocked
            continue

        except Exception as exc:
            print(f"[FALLBACK_SOURCE_GUARD] filter_error={exc}", flush=True)
            kept.append(order)

    return kept




# ============================================================
# QFOS ACTIVE POSITION WATCHDOG — DB-LEVEL EXIT PROTECTION
# Purpose:
#   1. Stop any single position from reaching oversized loss.
#   2. Protect positions that were in profit but give it back.
#   3. Prevent SIDEWAYS positions from staying open for 5+ hours
#      only to slide into loss.
#
# This does not depend on _qfos_exit_decision or proposed orders.
# It reads SQLite directly and closes paper positions directly.
# ============================================================

QFOS_POSITION_WATCHDOG_ENABLED = globals().get("QFOS_POSITION_WATCHDOG_ENABLED", True)
QFOS_POSITION_WATCHDOG_INTERVAL_SECONDS = globals().get("QFOS_POSITION_WATCHDOG_INTERVAL_SECONDS", 8.0)

# Single-position max loss, percentage of equity.
# 0.00045 = 0.045% equity. At $100 this is about $0.045.
QFOS_SINGLE_LOSS_CAP_SIDEWAYS_PCT = globals().get("QFOS_SINGLE_LOSS_CAP_SIDEWAYS_PCT", 0.00045)
QFOS_SINGLE_LOSS_CAP_TREND_PCT = globals().get("QFOS_SINGLE_LOSS_CAP_TREND_PCT", 0.00075)
QFOS_SINGLE_LOSS_CAP_RISK_OFF_PCT = globals().get("QFOS_SINGLE_LOSS_CAP_RISK_OFF_PCT", 0.00030)

# Profit giveback rules.
# If a position had at least this much profit and then gives most/all back, close it.
QFOS_MIN_PEAK_PROFIT_PCT = globals().get("QFOS_MIN_PEAK_PROFIT_PCT", 0.00018)      # $0.018 at $100
QFOS_PROFIT_GIVEBACK_FRACTION = globals().get("QFOS_PROFIT_GIVEBACK_FRACTION", 0.65)
QFOS_BREAKEVEN_GIVEBACK_BUFFER_PCT = globals().get("QFOS_BREAKEVEN_GIVEBACK_BUFFER_PCT", 0.00002)

# SIDEWAYS stale hold rules.
QFOS_SIDEWAYS_MAX_HOLD_MINUTES = globals().get("QFOS_SIDEWAYS_MAX_HOLD_MINUTES", 180.0)
QFOS_SIDEWAYS_STALE_NEGATIVE_MINUTES = globals().get("QFOS_SIDEWAYS_STALE_NEGATIVE_MINUTES", 120.0)

# Cooldown after watchdog close.
QFOS_POSITION_WATCHDOG_COOLDOWN_HOURS = globals().get("QFOS_POSITION_WATCHDOG_COOLDOWN_HOURS", 6.0)

def _qfos_poswd_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_poswd_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_poswd_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_poswd_parse_dt(value):
    from datetime import datetime
    if not value:
        return None
    s = str(value).replace("T", " ").replace("Z", "").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _qfos_poswd_cap_pct(regime):
    r = str(regime or "").upper()
    if r == "RISK_OFF":
        return QFOS_SINGLE_LOSS_CAP_RISK_OFF_PCT
    if r == "SIDEWAYS":
        return QFOS_SINGLE_LOSS_CAP_SIDEWAYS_PCT
    return QFOS_SINGLE_LOSS_CAP_TREND_PCT

def _qfos_poswd_ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_quarantine (
            symbol TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS strategy_quarantine (
            strategy TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS position_peak_state (
            symbol TEXT PRIMARY KEY,
            peak_unrealized_pnl REAL DEFAULT 0.0,
            first_seen_at TEXT,
            last_seen_at TEXT,
            last_reason TEXT
        )
    """)

def _qfos_poswd_latest_equity_and_regime(cur):
    try:
        row = cur.execute("""
            SELECT equity, regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        if row:
            equity = _qfos_poswd_float(row[0], 100.0)
            regime = str(row[1] or "SIDEWAYS")
            if equity > 0:
                return equity, regime
    except Exception:
        pass
    return 100.0, "SIDEWAYS"

def _qfos_poswd_latest_buy_time(cur, symbol):
    try:
        row = cur.execute("""
            SELECT created_at
            FROM trades
            WHERE symbol = ? AND side = 'buy'
            ORDER BY id DESC
            LIMIT 1
        """, (symbol,)).fetchone()
        if row:
            return _qfos_poswd_parse_dt(row[0])
    except Exception:
        pass
    return None

def _qfos_poswd_calc_unrealized(qty, avg_entry, mark, db_unrealized):
    qty = abs(_qfos_poswd_float(qty))
    entry = _qfos_poswd_float(avg_entry)
    price = _qfos_poswd_float(mark)
    db_u = _qfos_poswd_float(db_unrealized)

    if qty > 0 and entry > 0 and price > 0:
        return (price - entry) * qty

    return db_u

def _qfos_poswd_close_position(cur, pos, reason, pnl, now_s):
    from datetime import timedelta

    symbol = str(pos["symbol"])
    qty = abs(_qfos_poswd_float(pos["quantity"]))
    mark = _qfos_poswd_float(pos["last_price"])
    strategy = str(pos.get("strategy") or "unknown_strategy")

    blocked_until = (_qfos_poswd_now_local() + timedelta(hours=float(QFOS_POSITION_WATCHDOG_COOLDOWN_HOURS))).strftime("%Y-%m-%d %H:%M:%S")

    print(
        "[ACTIVE_POSITION_WATCHDOG] closing "
        f"symbol={symbol} reason={reason} qty={qty:.8f} mark={mark:.8f} "
        f"pnl={pnl:.6f}",
        flush=True,
    )

    cur.execute("""
        INSERT INTO trades (
            symbol, side, quantity, expected_price, fill_price,
            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
        )
        VALUES (?, 'sell', ?, ?, ?, 0.0, ?, ?, 1.0, 0, 0, ?)
    """, (symbol, qty, mark, mark, pnl, reason, now_s))

    cur.execute("""
        UPDATE positions
        SET quantity = 0.0,
            avg_entry = 0.0,
            realized_pnl = COALESCE(realized_pnl, 0.0) + ?,
            unrealized_pnl = 0.0,
            exposure = 0.0,
            strategy = ?,
            updated_at = ?
        WHERE symbol = ?
    """, (pnl, reason, now_s, symbol))

    cur.execute("""
        INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at)
        VALUES (?, ?, ?, ?)
    """, (symbol, reason, blocked_until, now_s))

    if strategy and strategy != "unknown_strategy":
        cur.execute("""
            INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at)
            VALUES (?, ?, ?, ?)
        """, (strategy, reason, blocked_until, now_s))

    cur.execute("""
        INSERT OR REPLACE INTO position_peak_state(symbol, peak_unrealized_pnl, first_seen_at, last_seen_at, last_reason)
        VALUES (?, 0.0, ?, ?, ?)
    """, (symbol, now_s, now_s, reason))

    print(
        "[ACTIVE_POSITION_WATCHDOG] closed "
        f"symbol={symbol} reason={reason} pnl={pnl:.6f} blocked_until={blocked_until}",
        flush=True,
    )

def _qfos_poswd_check_once():
    import sqlite3

    conn = sqlite3.connect(_qfos_poswd_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        _qfos_poswd_ensure_tables(cur)

        equity, regime = _qfos_poswd_latest_equity_and_regime(cur)
        now = _qfos_poswd_now_local()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")

        loss_cap = max(equity, 1.0) * _qfos_poswd_cap_pct(regime)
        min_peak_profit = max(equity, 1.0) * QFOS_MIN_PEAK_PROFIT_PCT
        breakeven_buffer = max(equity, 1.0) * QFOS_BREAKEVEN_GIVEBACK_BUFFER_PCT

        rows = cur.execute("""
            SELECT symbol, quantity, avg_entry, last_price, exposure, realized_pnl, unrealized_pnl, strategy, updated_at
            FROM positions
            WHERE quantity > 0
        """).fetchall()

        for row in rows:
            pos = dict(row)
            symbol = str(pos["symbol"])
            qty = _qfos_poswd_float(pos["quantity"])
            avg_entry = _qfos_poswd_float(pos["avg_entry"])
            last_price = _qfos_poswd_float(pos["last_price"])
            unrealized = _qfos_poswd_calc_unrealized(qty, avg_entry, last_price, pos.get("unrealized_pnl"))

            buy_time = _qfos_poswd_latest_buy_time(cur, symbol)
            age_minutes = 0.0
            if buy_time:
                age_minutes = max((now - buy_time).total_seconds() / 60.0, 0.0)

            peak_row = cur.execute("""
                SELECT peak_unrealized_pnl, first_seen_at
                FROM position_peak_state
                WHERE symbol = ?
            """, (symbol,)).fetchone()

            if peak_row:
                old_peak = _qfos_poswd_float(peak_row["peak_unrealized_pnl"])
                first_seen = str(peak_row["first_seen_at"] or now_s)
            else:
                old_peak = unrealized
                first_seen = now_s

            peak = max(old_peak, unrealized)

            cur.execute("""
                INSERT OR REPLACE INTO position_peak_state(symbol, peak_unrealized_pnl, first_seen_at, last_seen_at, last_reason)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, peak, first_seen, now_s, "tracking"))

            loss = max(-unrealized, 0.0)

            # 1) Hard single-position loss cap.
            if loss >= loss_cap:
                _qfos_poswd_close_position(
                    cur,
                    pos,
                    "single_loss_cap",
                    -abs(loss),
                    now_s,
                )
                conn.commit()
                return

            # 2) Profit giveback: position had meaningful profit, then gave most back or went red.
            if peak >= min_peak_profit:
                giveback = peak - unrealized
                giveback_trigger = max(peak * QFOS_PROFIT_GIVEBACK_FRACTION, min_peak_profit)

                if giveback >= giveback_trigger and unrealized <= breakeven_buffer:
                    pnl = unrealized
                    _qfos_poswd_close_position(
                        cur,
                        pos,
                        "profit_giveback_exit",
                        pnl,
                        now_s,
                    )
                    conn.commit()
                    return

            # 3) SIDEWAYS stale hold: no reason to hold weak/flat positions for 5+ hours.
            if str(regime).upper() == "SIDEWAYS":
                if age_minutes >= QFOS_SIDEWAYS_MAX_HOLD_MINUTES and unrealized <= min_peak_profit:
                    _qfos_poswd_close_position(
                        cur,
                        pos,
                        "sideways_max_hold_exit",
                        unrealized,
                        now_s,
                    )
                    conn.commit()
                    return

                if age_minutes >= QFOS_SIDEWAYS_STALE_NEGATIVE_MINUTES and unrealized < 0:
                    _qfos_poswd_close_position(
                        cur,
                        pos,
                        "sideways_stale_negative_exit",
                        unrealized,
                        now_s,
                    )
                    conn.commit()
                    return

        conn.commit()

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[ACTIVE_POSITION_WATCHDOG] error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_poswd_loop():
    import time
    while True:
        try:
            if QFOS_POSITION_WATCHDOG_ENABLED:
                _qfos_poswd_check_once()
        except Exception as exc:
            print(f"[ACTIVE_POSITION_WATCHDOG] loop_error={exc}", flush=True)
        time.sleep(float(QFOS_POSITION_WATCHDOG_INTERVAL_SECONDS))

def _qfos_start_active_position_watchdog():
    try:
        import threading
        if globals().get("_QFOS_ACTIVE_POSITION_WATCHDOG_STARTED"):
            return
        globals()["_QFOS_ACTIVE_POSITION_WATCHDOG_STARTED"] = True
        t = threading.Thread(target=_qfos_poswd_loop, daemon=True, name="qfos_active_position_watchdog")
        t.start()
        print("[ACTIVE_POSITION_WATCHDOG] started", flush=True)
    except Exception as exc:
        print(f"[ACTIVE_POSITION_WATCHDOG] start_error={exc}", flush=True)

_qfos_start_active_position_watchdog()




# ============================================================
# QFOS PROFIT ENGINE V1 — TRADE CLASS + PARTIAL TP + RUNNERS
# Purpose:
#   Fix negative expectancy caused by tiny full exits and larger
#   losses. This engine separates fallback, sideways scalp, and
#   quality breakout/trend behavior.
#
# It is DB-level and does not depend on _qfos_exit_decision.
# Existing watchdogs remain active.
# ============================================================

QFOS_PROFIT_ENGINE_ENABLED = globals().get("QFOS_PROFIT_ENGINE_ENABLED", True)
QFOS_PROFIT_ENGINE_INTERVAL_SECONDS = globals().get("QFOS_PROFIT_ENGINE_INTERVAL_SECONDS", 8.0)

# Position sizing targets for FUTURE use/logging. This engine mostly manages exits.
QFOS_SIZE_FALLBACK_PCT = globals().get("QFOS_SIZE_FALLBACK_PCT", 0.005)       # 0.5% equity
QFOS_SIZE_SIDEWAYS_PCT = globals().get("QFOS_SIZE_SIDEWAYS_PCT", 0.015)       # 1.5% equity
QFOS_SIZE_QUALITY_PCT = globals().get("QFOS_SIZE_QUALITY_PCT", 0.030)         # 3.0% equity

# FALLBACK_SCOUT: probe only, no runner.
QFOS_FB_TP_PCT = globals().get("QFOS_FB_TP_PCT", 0.0060)                     # +0.60%
QFOS_FB_SL_PCT = globals().get("QFOS_FB_SL_PCT", -0.0035)                    # -0.35%
QFOS_FB_MAX_HOLD_MIN = globals().get("QFOS_FB_MAX_HOLD_MIN", 20.0)

# SIDEWAYS_SCALP: scalp only, no fantasy runner.
QFOS_SW_TP_PCT = globals().get("QFOS_SW_TP_PCT", 0.0090)                     # +0.80%
QFOS_SW_SL_PCT = globals().get("QFOS_SW_SL_PCT", -0.0045)                    # -0.45%
QFOS_SW_BREAKEVEN_TRIGGER_PCT = globals().get("QFOS_SW_BREAKEVEN_TRIGGER_PCT", 0.0035)
QFOS_SW_PROFIT_GIVEBACK_TRIGGER_PCT = globals().get("QFOS_SW_PROFIT_GIVEBACK_TRIGGER_PCT", 0.0070)
QFOS_SW_PROFIT_FLOOR_PCT = globals().get("QFOS_SW_PROFIT_FLOOR_PCT", 0.0030)
QFOS_SW_MAX_HOLD_MIN = globals().get("QFOS_SW_MAX_HOLD_MIN", 45.0)

# QUALITY_TREND_OR_BREAKOUT: partial + protected runner.
QFOS_Q_PARTIAL_TP_PCT = globals().get("QFOS_Q_PARTIAL_TP_PCT", 0.0120)        # +1.00%
QFOS_Q_PARTIAL_FRACTION = globals().get("QFOS_Q_PARTIAL_FRACTION", 1.0)
QFOS_Q_INITIAL_SL_PCT = globals().get("QFOS_Q_INITIAL_SL_PCT", -0.0070)       # -0.70%
QFOS_Q_RUNNER_BE_PCT = globals().get("QFOS_Q_RUNNER_BE_PCT", 0.0010)          # +0.10%
QFOS_Q_TRAIL_ACTIVATION_PCT = globals().get("QFOS_Q_TRAIL_ACTIVATION_PCT", 0.0120)
QFOS_Q_TRAIL_GIVEBACK_FRACTION = globals().get("QFOS_Q_TRAIL_GIVEBACK_FRACTION", 0.60)
QFOS_Q_MAX_HOLD_MIN = globals().get("QFOS_Q_MAX_HOLD_MIN", 240.0)

# If position is green then goes weak, do not let it become a meaningful loser.
QFOS_GREEN_TO_RED_BUFFER_PCT = globals().get("QFOS_GREEN_TO_RED_BUFFER_PCT", 0.0005)

# Cooldown after profit-engine exit.
QFOS_PROFIT_ENGINE_COOLDOWN_HOURS = globals().get("QFOS_PROFIT_ENGINE_COOLDOWN_HOURS", 4.0)

def _qfos_pe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_pe_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_pe_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_pe_parse_dt(value):
    from datetime import datetime
    if not value:
        return None
    s = str(value).replace("T", " ").replace("Z", "").split(".")[0]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            pass
    return None

def _qfos_pe_ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profit_engine_state (
            symbol TEXT PRIMARY KEY,
            trade_class TEXT,
            entry_qty REAL,
            partial_taken INTEGER DEFAULT 0,
            peak_unrealized_pnl REAL DEFAULT 0.0,
            worst_unrealized_pnl REAL DEFAULT 0.0,
            first_seen_at TEXT,
            last_seen_at TEXT,
            last_action TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_quarantine (
            symbol TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS strategy_quarantine (
            strategy TEXT PRIMARY KEY,
            reason TEXT,
            blocked_until TEXT,
            created_at TEXT
        )
    """)

def _qfos_pe_latest_equity_and_regime(cur):
    try:
        row = cur.execute("""
            SELECT equity, regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        if row:
            equity = _qfos_pe_float(row[0], 100.0)
            regime = str(row[1] or "SIDEWAYS")
            if equity > 0:
                return equity, regime
    except Exception:
        pass
    return 100.0, "SIDEWAYS"

def _qfos_pe_latest_buy(cur, symbol):
    try:
        row = cur.execute("""
            SELECT id, quantity, fill_price, strategy, created_at
            FROM trades
            WHERE symbol = ? AND side = 'buy'
            ORDER BY id DESC
            LIMIT 1
        """, (symbol,)).fetchone()
        return row
    except Exception:
        return None

def _qfos_pe_classify(symbol, position_strategy, latest_buy_strategy, exposure, equity, global_regime):
    """
    Profit Engine classifier rewritten by POLICY V2.

    FALLBACK_SCOUT:
      Always tiny, fast, no runner unless exceptional.

    SIDEWAYS_SCALP:
      Default for weak/medium trades in SIDEWAYS.

    QUALITY_TREND_OR_BREAKOUT:
      Only for strong symbol-level trend/breakout features.
    """
    strategy = f"{position_strategy or ''} {latest_buy_strategy or ''}".strip()

    exposure_pct = 0.0
    try:
        exposure_pct = float(exposure or 0.0) / max(float(equity or 100.0), 1.0)
    except Exception:
        exposure_pct = 0.0

    try:
        return _qfos_v2_symbol_quality_class(
            symbol=str(symbol),
            strategy=strategy,
            global_regime=str(global_regime or "SIDEWAYS"),
            exposure_pct=exposure_pct,
        )
    except Exception as exc:
        print(f"[POLICY_V2] classify_fallback_error symbol={symbol} err={exc}", flush=True)
        if "fallback" in strategy.lower() or "scout" in strategy.lower():
            return "FALLBACK_SCOUT"
        if str(global_regime).upper() == "SIDEWAYS":
            return "SIDEWAYS_SCALP"
        return "QUALITY_TREND_OR_BREAKOUT"


def _qfos_pe_unrealized(qty, avg_entry, last_price, db_unrealized):
    q = abs(_qfos_pe_float(qty))
    entry = _qfos_pe_float(avg_entry)
    mark = _qfos_pe_float(last_price)
    if q > 0 and entry > 0 and mark > 0:
        return (mark - entry) * q
    return _qfos_pe_float(db_unrealized)

def _qfos_pe_return_pct(avg_entry, last_price):
    entry = _qfos_pe_float(avg_entry)
    mark = _qfos_pe_float(last_price)
    if entry <= 0 or mark <= 0:
        return 0.0
    return (mark - entry) / entry

def _qfos_pe_quarantine(cur, symbol, strategy, reason, now_s):
    from datetime import timedelta
    blocked_until = (_qfos_pe_now_local() + timedelta(hours=float(QFOS_PROFIT_ENGINE_COOLDOWN_HOURS))).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at)
        VALUES (?, ?, ?, ?)
    """, (symbol, reason, blocked_until, now_s))
    if strategy:
        cur.execute("""
            INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at)
            VALUES (?, ?, ?, ?)
        """, (strategy, reason, blocked_until, now_s))
    return blocked_until

def _qfos_pe_sell(cur, pos, quantity, reason, pnl, now_s, quarantine=True):
    symbol = str(pos["symbol"])
    qty = abs(_qfos_pe_float(quantity))
    mark = _qfos_pe_float(pos["last_price"])
    strategy = str(pos.get("strategy") or reason)

    if qty <= 0:
        return False

    print(
        "[PROFIT_ENGINE] selling "
        f"symbol={symbol} reason={reason} qty={qty:.8f} mark={mark:.8f} pnl={pnl:.6f}",
        flush=True,
    )

    cur.execute("""
        INSERT INTO trades (
            symbol, side, quantity, expected_price, fill_price,
            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
        )
        VALUES (?, 'sell', ?, ?, ?, 0.0, ?, ?, 1.0, 0, 0, ?)
    """, (symbol, qty, mark, mark, pnl, reason, now_s))

    old_qty = abs(_qfos_pe_float(pos["quantity"]))
    new_qty = max(old_qty - qty, 0.0)
    new_exposure = new_qty * mark

    if new_qty <= 1e-12:
        cur.execute("""
            UPDATE positions
            SET quantity = 0.0,
                avg_entry = 0.0,
                realized_pnl = COALESCE(realized_pnl, 0.0) + ?,
                unrealized_pnl = 0.0,
                exposure = 0.0,
                strategy = ?,
                updated_at = ?
            WHERE symbol = ?
        """, (pnl, reason, now_s, symbol))

        if quarantine:
            blocked_until = _qfos_pe_quarantine(cur, symbol, strategy, reason, now_s)
            print(f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason} blocked_until={blocked_until}", flush=True)
        else:
            print(f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason}", flush=True)

    else:
        cur.execute("""
            UPDATE positions
            SET quantity = ?,
                realized_pnl = COALESCE(realized_pnl, 0.0) + ?,
                unrealized_pnl = ?,
                exposure = ?,
                updated_at = ?
            WHERE symbol = ?
        """, (new_qty, pnl, 0.0, new_exposure, now_s, symbol))

        print(f"[PROFIT_ENGINE] partial sold symbol={symbol} remaining_qty={new_qty:.8f}", flush=True)

    return True

def _qfos_pe_check_once():
    import sqlite3

    conn = sqlite3.connect(_qfos_pe_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        _qfos_pe_ensure_tables(cur)

        equity, global_regime = _qfos_pe_latest_equity_and_regime(cur)
        now = _qfos_pe_now_local()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")

        rows = cur.execute("""
            SELECT symbol, quantity, avg_entry, last_price, exposure, realized_pnl, unrealized_pnl, strategy, updated_at
            FROM positions
            WHERE quantity > 0
        """).fetchall()

        # QFOS_SIDEWAYS_HARD_EXPOSURE_REDUCER_V2
        try:
            if _qfos_guard_profit_engine_reduce_if_needed(cur, rows, equity, global_regime, now_s):
                conn.commit()
                return
        except Exception as _qfos_pe_guard_error:
            print(f"[PROFIT_ENGINE_GUARD] reducer_outer_error={_qfos_pe_guard_error}", flush=True)

        for row in rows:
            pos = dict(row)
            symbol = str(pos["symbol"])
            qty = abs(_qfos_pe_float(pos["quantity"]))
            avg_entry = _qfos_pe_float(pos["avg_entry"])
            last_price = _qfos_pe_float(pos["last_price"])
            exposure = _qfos_pe_float(pos["exposure"])
            strategy = str(pos.get("strategy") or "")

            if qty <= 0 or avg_entry <= 0 or last_price <= 0:
                continue

            buy = _qfos_pe_latest_buy(cur, symbol)
            latest_buy_strategy = None
            buy_time = None
            buy_qty = qty
            if buy:
                latest_buy_strategy = buy["strategy"]
                buy_time = _qfos_pe_parse_dt(buy["created_at"])
                buy_qty = abs(_qfos_pe_float(buy["quantity"], qty))

            age_min = 0.0
            if buy_time:
                age_min = max((now - buy_time).total_seconds() / 60.0, 0.0)

            trade_class = _qfos_pe_classify(
                symbol,
                strategy,
                latest_buy_strategy,
                exposure,
                equity,
                global_regime,
            )

            unrealized = _qfos_pe_unrealized(qty, avg_entry, last_price, pos.get("unrealized_pnl"))
            ret_pct = _qfos_pe_return_pct(avg_entry, last_price)

            state = cur.execute("""
                SELECT trade_class, entry_qty, partial_taken, peak_unrealized_pnl, worst_unrealized_pnl, first_seen_at
                FROM profit_engine_state
                WHERE symbol = ?
            """, (symbol,)).fetchone()

            if state:
                entry_qty = _qfos_pe_float(state["entry_qty"], buy_qty or qty)
                partial_taken = int(state["partial_taken"] or 0)
                peak = max(_qfos_pe_float(state["peak_unrealized_pnl"]), unrealized)
                worst = min(_qfos_pe_float(state["worst_unrealized_pnl"]), unrealized)
                first_seen = str(state["first_seen_at"] or now_s)
                if state["trade_class"]:
                    trade_class = str(state["trade_class"])
            else:
                entry_qty = buy_qty or qty
                partial_taken = 0
                peak = max(unrealized, 0.0)
                worst = min(unrealized, 0.0)
                first_seen = now_s

            cur.execute("""
                INSERT OR REPLACE INTO profit_engine_state(
                    symbol, trade_class, entry_qty, partial_taken,
                    peak_unrealized_pnl, worst_unrealized_pnl,
                    first_seen_at, last_seen_at, last_action
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, trade_class, entry_qty, partial_taken, peak, worst, first_seen, now_s, "tracking"))

            print(
                "[PROFIT_ENGINE] track "
                f"symbol={symbol} class={trade_class} ret={ret_pct:.4%} "
                f"unreal={unrealized:.6f} peak={peak:.6f} age_min={age_min:.1f}",
                flush=True,
            )



            # QFOS_SIDEWAYS_HARD_EXPOSURE_CLEANUP_INSTALLED
            try:
                _qfos_cleanup_regime = None
                _qfos_cleanup_exposure_pct = 0.0

                try:
                    _qfos_cleanup_regime = regime
                except Exception:
                    _qfos_cleanup_regime = None

                try:
                    _qfos_cleanup_exposure_pct = exposure_pct
                except Exception:
                    _qfos_cleanup_exposure_pct = 0.0

                # Fallback: infer exposure pct from this position if globals are unavailable.
                if not _qfos_cleanup_exposure_pct:
                    try:
                        _qfos_port = globals().get("portfolio")
                        if isinstance(_qfos_port, dict):
                            _qfos_cleanup_exposure_pct = _qfos_guard_float(_qfos_port.get("exposure_pct", 0.0))
                            _qfos_cleanup_regime = _qfos_cleanup_regime or _qfos_port.get("regime")
                    except Exception:
                        pass

                if _qfos_guard_overexposed(_qfos_cleanup_regime, _qfos_cleanup_exposure_pct):
                    _qfos_this_is_stale = str(trade_class).upper() == QFOS_STALE_CLASS_FROM
                    _qfos_this_is_loser = (unrealized < 0 or ret_pct < 0)

                    if _qfos_this_is_stale or _qfos_this_is_loser:
                        _qfos_guard_log_watchdog(
                            _qfos_cleanup_regime,
                            _qfos_cleanup_exposure_pct,
                            symbol=symbol,
                            action="CLOSE_WEAKEST",
                            reason=f"sideways_hard_exposure_guard stale={_qfos_this_is_stale} loser={_qfos_this_is_loser}",
                        )

                        _qfos_pe_sell(cur, pos, qty, "sideways_hard_exposure_guard", unrealized, now_s, quarantine=False)
                        conn.commit()
                        return
                    else:
                        _qfos_guard_log_watchdog(
                            _qfos_cleanup_regime,
                            _qfos_cleanup_exposure_pct,
                            symbol=symbol,
                            action="BLOCK_NEW_ENTRIES_ONLY",
                            reason="overexposed but current inspected position is not weak/stale",
                        )

            except Exception as _qfos_cleanup_e:
                print(f"[PROFIT_ENGINE_GUARD] cleanup_outer_error symbol={symbol} error={_qfos_cleanup_e}", flush=True)


            # QFOS_STALE_CLASS_DOWNGRADE_INSTALLED
            try:
                _qfos_current_policy = None

                try:
                    _qfos_current_policy = _qfos_guard_policy_class_from_features(
                        symbol,
                        result_f_by_symbol=globals().get("result_f_by_symbol"),
                        features=globals().get("features"),
                    )
                except Exception:
                    _qfos_current_policy = None

                if _qfos_current_policy is None:
                    try:
                        _qfos_regime_for_policy = str(global_regime).upper()
                    except Exception:
                        _qfos_regime_for_policy = ""

                    if _qfos_regime_for_policy == "SIDEWAYS":
                        _qfos_current_policy = "SIDEWAYS_SCALP"

                _qfos_new_trade_class = _qfos_guard_downgrade_trade_class(
                    symbol,
                    trade_class,
                    _qfos_current_policy,
                )

                if _qfos_new_trade_class != trade_class:
                    trade_class = _qfos_new_trade_class
                    try:
                        cur.execute(
                            "UPDATE profit_engine_state SET trade_class=?, last_action=? WHERE symbol=?",
                            (trade_class, "stale_class_downgraded", symbol),
                        )
                        conn.commit()
                    except Exception as _qfos_db_e:
                        print(f"[PROFIT_ENGINE_GUARD] stale_class_db_update_error symbol={symbol} error={_qfos_db_e}", flush=True)

            except Exception as _qfos_e:
                print(f"[PROFIT_ENGINE_GUARD] stale_class_outer_error symbol={symbol} error={_qfos_e}", flush=True)


            # -------------------------------
            # FALLBACK_SCOUT
            # -------------------------------
            if trade_class == "FALLBACK_SCOUT":
                if ret_pct >= QFOS_FB_TP_PCT:
                    _qfos_pe_sell(cur, pos, qty, "fallback_take_profit", unrealized, now_s, quarantine=False)
                    conn.commit()
                    return
                if ret_pct <= QFOS_FB_SL_PCT:
                    _qfos_pe_sell(cur, pos, qty, "fallback_stop_loss", unrealized, now_s, quarantine=True)
                    conn.commit()
                    return
                if age_min >= QFOS_FB_MAX_HOLD_MIN:
                    _qfos_pe_sell(cur, pos, qty, "fallback_max_hold_exit", unrealized, now_s, quarantine=False)
                    conn.commit()
                    return

            # -------------------------------
            # SIDEWAYS_SCALP
            # -------------------------------
            elif trade_class == "SIDEWAYS_SCALP":
                if ret_pct >= QFOS_SW_TP_PCT:
                    _qfos_pe_sell(cur, pos, qty, "sideways_scalp_take_profit", unrealized, now_s, quarantine=False)
                    conn.commit()
                    return

                if ret_pct <= QFOS_SW_SL_PCT:
                    _qfos_pe_sell(cur, pos, qty, "sideways_scalp_stop_loss", unrealized, now_s, quarantine=True)
                    conn.commit()
                    return

                if peak >= exposure * QFOS_SW_PROFIT_GIVEBACK_TRIGGER_PCT:
                    floor = exposure * QFOS_SW_PROFIT_FLOOR_PCT
                    if unrealized <= floor:
                        _qfos_pe_sell(cur, pos, qty, "sideways_green_to_red_exit", unrealized, now_s, quarantine=False)
                        conn.commit()
                        return

                if age_min >= QFOS_SW_MAX_HOLD_MIN:
                    _qfos_pe_sell(cur, pos, qty, "sideways_max_hold_profit_engine", unrealized, now_s, quarantine=False)
                    conn.commit()
                    return

            # -------------------------------
            # QUALITY_TREND_OR_BREAKOUT
            # -------------------------------
            else:
                # Initial hard SL for quality trades.
                if ret_pct <= QFOS_Q_INITIAL_SL_PCT:
                    _qfos_pe_sell(cur, pos, qty, "quality_initial_stop_loss", unrealized, now_s, quarantine=True)
                    conn.commit()
                    return

                # Partial TP once.
                if partial_taken == 0 and ret_pct >= QFOS_Q_PARTIAL_TP_PCT:
                    sell_qty = min(qty, max(entry_qty * QFOS_Q_PARTIAL_FRACTION, qty * QFOS_Q_PARTIAL_FRACTION))
                    pnl_fraction = sell_qty / max(qty, 1e-12)
                    sell_pnl = unrealized * pnl_fraction
                    if _qfos_pe_sell(cur, pos, sell_qty, "quality_full_take_profit", sell_pnl, now_s, quarantine=False):
                        cur.execute("""
                            UPDATE profit_engine_state
                            SET partial_taken = 1,
                                last_action = 'quality_full_take_profit',
                                last_seen_at = ?
                            WHERE symbol = ?
                        """, (now_s, symbol))
                        conn.commit()
                        return

                # After partial, protect runner: breakeven + small profit floor.
                if partial_taken == 1:
                    if ret_pct <= QFOS_Q_RUNNER_BE_PCT:
                        _qfos_pe_sell(cur, pos, qty, "quality_runner_breakeven_exit", unrealized, now_s, quarantine=False)
                        conn.commit()
                        return

                    if ret_pct >= QFOS_Q_TRAIL_ACTIVATION_PCT:
                        giveback = peak - unrealized
                        if peak > 0 and giveback >= peak * QFOS_Q_TRAIL_GIVEBACK_FRACTION:
                            _qfos_pe_sell(cur, pos, qty, "quality_runner_trailing_exit", unrealized, now_s, quarantine=False)
                            conn.commit()
                            return

                if age_min >= QFOS_Q_MAX_HOLD_MIN and partial_taken == 0:
                    # If a "quality" trade did not achieve partial TP in time, stop treating it like quality.
                    _qfos_pe_sell(cur, pos, qty, "quality_failed_to_run_exit", unrealized, now_s, quarantine=False)
                    conn.commit()
                    return

        conn.commit()

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[PROFIT_ENGINE] error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_pe_loop():
    import time
    while True:
        try:
            if QFOS_PROFIT_ENGINE_ENABLED:
                _qfos_pe_check_once()
        except Exception as exc:
            print(f"[PROFIT_ENGINE] loop_error={exc}", flush=True)
        time.sleep(float(QFOS_PROFIT_ENGINE_INTERVAL_SECONDS))

def _qfos_start_profit_engine():
    try:
        import threading
        if globals().get("_QFOS_PROFIT_ENGINE_STARTED"):
            return
        globals()["_QFOS_PROFIT_ENGINE_STARTED"] = True
        t = threading.Thread(target=_qfos_pe_loop, daemon=True, name="qfos_profit_engine_v1")
        t.start()
        print("[PROFIT_ENGINE] started", flush=True)
    except Exception as exc:
        print(f"[PROFIT_ENGINE] start_error={exc}", flush=True)

_qfos_start_profit_engine()




# ============================================================
# QFOS PORTFOLIO ACCOUNTING RECONCILER
# Purpose:
#   DB-level watchdog/profit-engine exits correctly close paper
#   positions, but they may bypass the normal cash/equity update.
#   This reconciler fixes portfolio_snapshots from trades + positions.
#
# Formula:
#   realized_pnl   = SUM(trades.pnl)
#   unrealized_pnl = SUM(open positions.unrealized_pnl)
#   exposure       = SUM(open positions.exposure or qty * last_price)
#   equity         = starting_equity + realized_pnl + unrealized_pnl
#   cash           = equity - exposure
# ============================================================

QFOS_PORTFOLIO_RECONCILER_ENABLED = globals().get("QFOS_PORTFOLIO_RECONCILER_ENABLED", True)
QFOS_PORTFOLIO_RECONCILER_INTERVAL_SECONDS = globals().get("QFOS_PORTFOLIO_RECONCILER_INTERVAL_SECONDS", 6.0)
QFOS_STARTING_EQUITY = globals().get("QFOS_STARTING_EQUITY", 100.0)

def _qfos_acct_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_acct_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_acct_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_acct_table_columns(cur, table):
    try:
        return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []

def _qfos_acct_latest_regime(cur):
    try:
        cols = _qfos_acct_table_columns(cur, "portfolio_snapshots")
        if "regime" not in cols:
            return "SIDEWAYS"
        row = cur.execute("""
            SELECT regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return "SIDEWAYS"

def _qfos_acct_realized_pnl(cur):
    try:
        row = cur.execute("SELECT COALESCE(SUM(pnl), 0.0) FROM trades").fetchone()
        return _qfos_acct_float(row[0] if row else 0.0, 0.0)
    except Exception:
        return 0.0

def _qfos_acct_open_position_totals(cur):
    exposure = 0.0
    unrealized = 0.0

    try:
        rows = cur.execute("""
            SELECT quantity, avg_entry, last_price, exposure, unrealized_pnl
            FROM positions
            WHERE quantity > 0
        """).fetchall()

        for qty, avg_entry, last_price, db_exposure, db_unrealized in rows:
            q = abs(_qfos_acct_float(qty))
            entry = _qfos_acct_float(avg_entry)
            mark = _qfos_acct_float(last_price)

            calc_exposure = q * mark if q > 0 and mark > 0 else 0.0
            exposure += max(_qfos_acct_float(db_exposure), calc_exposure, 0.0)

            if q > 0 and entry > 0 and mark > 0:
                unrealized += (mark - entry) * q
            else:
                unrealized += _qfos_acct_float(db_unrealized)
    except Exception as exc:
        print(f"[PORTFOLIO_RECONCILER] position_total_error={exc}", flush=True)

    return exposure, unrealized

def _qfos_acct_ensure_snapshot_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equity REAL,
            cash REAL,
            exposure REAL,
            drawdown REAL,
            regime TEXT,
            created_at TEXT
        )
    """)

def _qfos_acct_insert_snapshot(cur, equity, cash, exposure, drawdown, regime, now_s):
    cols = _qfos_acct_table_columns(cur, "portfolio_snapshots")
    if not cols:
        _qfos_acct_ensure_snapshot_table(cur)
        cols = _qfos_acct_table_columns(cur, "portfolio_snapshots")

    values = {}
    if "equity" in cols:
        values["equity"] = equity
    if "cash" in cols:
        values["cash"] = cash
    if "exposure" in cols:
        values["exposure"] = exposure
    if "drawdown" in cols:
        values["drawdown"] = drawdown
    if "regime" in cols:
        values["regime"] = regime
    if "created_at" in cols:
        values["created_at"] = now_s
    if "updated_at" in cols:
        values["updated_at"] = now_s
    if "realized_pnl" in cols:
        values["realized_pnl"] = equity - QFOS_STARTING_EQUITY
    if "unrealized_pnl" in cols:
        values["unrealized_pnl"] = 0.0

    if not values:
        return

    col_names = list(values.keys())
    placeholders = ",".join(["?"] * len(col_names))
    sql = f"INSERT INTO portfolio_snapshots ({','.join(col_names)}) VALUES ({placeholders})"
    cur.execute(sql, [values[c] for c in col_names])

def _qfos_acct_reconcile_once(verbose=False):
    import sqlite3

    conn = sqlite3.connect(_qfos_acct_db_path(), timeout=10)
    cur = conn.cursor()

    try:
        _qfos_acct_ensure_snapshot_table(cur)

        now = _qfos_acct_now_local()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")

        regime = _qfos_acct_latest_regime(cur)
        realized = _qfos_acct_realized_pnl(cur)
        exposure, unrealized = _qfos_acct_open_position_totals(cur)

        equity = float(QFOS_STARTING_EQUITY) + realized + unrealized
        cash = equity - exposure
        drawdown = (equity - float(QFOS_STARTING_EQUITY)) / max(float(QFOS_STARTING_EQUITY), 1.0)

        _qfos_acct_insert_snapshot(
            cur,
            round(equity, 8),
            round(cash, 8),
            round(exposure, 8),
            round(drawdown, 8),
            regime,
            now_s,
        )

        conn.commit()

        if verbose:
            print(
                "[PORTFOLIO_RECONCILER] synced "
                f"equity={equity:.4f} cash={cash:.4f} exposure={exposure:.4f} "
                f"realized={realized:.4f} unrealized={unrealized:.4f} "
                f"drawdown={drawdown:.4%} regime={regime}",
                flush=True,
            )

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[PORTFOLIO_RECONCILER] error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_acct_loop():
    import time
    tick = 0
    while True:
        try:
            if QFOS_PORTFOLIO_RECONCILER_ENABLED:
                tick += 1
                _qfos_acct_reconcile_once(verbose=(tick % 5 == 1))
        except Exception as exc:
            print(f"[PORTFOLIO_RECONCILER] loop_error={exc}", flush=True)
        time.sleep(float(QFOS_PORTFOLIO_RECONCILER_INTERVAL_SECONDS))

def _qfos_start_portfolio_reconciler():
    try:
        import threading
        if globals().get("_QFOS_PORTFOLIO_RECONCILER_STARTED"):
            return
        globals()["_QFOS_PORTFOLIO_RECONCILER_STARTED"] = True
        # One immediate sync before thread.
        _qfos_acct_reconcile_once(verbose=True)
        t = threading.Thread(target=_qfos_acct_loop, daemon=True, name="qfos_portfolio_reconciler")
        t.start()
        print("[PORTFOLIO_RECONCILER] started", flush=True)
    except Exception as exc:
        print(f"[PORTFOLIO_RECONCILER] start_error={exc}", flush=True)

_qfos_start_portfolio_reconciler()




# ============================================================
# QFOS POLICY V2 — CONSOLIDATED ENTRY CLASSIFICATION + RUNNER LOGIC
# Purpose:
#   Fix confused scalper/trend-following behavior.
#
# Rules:
#   FALLBACK_SCOUT:
#     - Always tiny, fast, no runner.
#     - Can only upgrade to quality if exceptionally strong.
#
#   SIDEWAYS_SCALP:
#     - Default class for weak/medium trades during global SIDEWAYS.
#     - Tight TP/SL and short hold.
#
#   QUALITY_TREND_OR_BREAKOUT:
#     - Only if symbol-level features are strong enough.
#     - Uses partial TP + runner.
#
# This block does not replace your app loop/API.
# It rewires the policy layer around the existing main.py.
# ============================================================

QFOS_POLICY_V2_ENABLED = globals().get("QFOS_POLICY_V2_ENABLED", True)

# Quality upgrade thresholds for normal non-fallback trades.
QFOS_V2_QUALITY_SIGNAL_MIN = globals().get("QFOS_V2_QUALITY_SIGNAL_MIN", 0.0065)
QFOS_V2_QUALITY_BREAKOUT_MIN = globals().get("QFOS_V2_QUALITY_BREAKOUT_MIN", 0.0055)
QFOS_V2_QUALITY_TREND_QUALITY_MIN = globals().get("QFOS_V2_QUALITY_TREND_QUALITY_MIN", 0.00001)
QFOS_V2_QUALITY_MOMENTUM_MIN = globals().get("QFOS_V2_QUALITY_MOMENTUM_MIN", 0.00050)
QFOS_V2_QUALITY_ONE_TICK_MIN = globals().get("QFOS_V2_QUALITY_ONE_TICK_MIN", 0.00005)

# Exceptional fallback upgrade thresholds.
# Fallback should almost never become runner mode.
QFOS_V2_FALLBACK_EXCEPTIONAL_SIGNAL_MIN = globals().get("QFOS_V2_FALLBACK_EXCEPTIONAL_SIGNAL_MIN", 0.0120)
QFOS_V2_FALLBACK_EXCEPTIONAL_BREAKOUT_MIN = globals().get("QFOS_V2_FALLBACK_EXCEPTIONAL_BREAKOUT_MIN", 0.0100)
QFOS_V2_FALLBACK_EXCEPTIONAL_TREND_QUALITY_MIN = globals().get("QFOS_V2_FALLBACK_EXCEPTIONAL_TREND_QUALITY_MIN", 0.0100)
QFOS_V2_FALLBACK_EXCEPTIONAL_MOMENTUM_MIN = globals().get("QFOS_V2_FALLBACK_EXCEPTIONAL_MOMENTUM_MIN", 0.00150)
QFOS_V2_FALLBACK_EXCEPTIONAL_ONE_TICK_MIN = globals().get("QFOS_V2_FALLBACK_EXCEPTIONAL_ONE_TICK_MIN", 0.00020)

# Fallback source guard: block weak fallback before it becomes an order.
QFOS_V2_BLOCK_WEAK_FALLBACK_SOURCE = globals().get("QFOS_V2_BLOCK_WEAK_FALLBACK_SOURCE", True)

def _qfos_v2_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_v2_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_v2_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_v2_ensure_feature_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_feature_snapshot (
            symbol TEXT PRIMARY KEY,
            price REAL,
            trend REAL,
            long_trend REAL,
            volatility REAL,
            momentum REAL,
            one_tick_momentum REAL,
            signal_strength REAL,
            symbol_regime TEXT,
            symbol_trend_score REAL,
            breakout_score REAL,
            trend_quality REAL,
            is_symbol_uptrend INTEGER,
            is_symbol_downtrend INTEGER,
            is_choppy INTEGER,
            updated_at TEXT
        )
    """)

def _qfos_v2_feature_to_row(symbol, f, now_s):
    if not isinstance(f, dict):
        f = {}
    return (
        symbol,
        _qfos_v2_float(f.get("price")),
        _qfos_v2_float(f.get("trend")),
        _qfos_v2_float(f.get("long_trend")),
        _qfos_v2_float(f.get("volatility")),
        _qfos_v2_float(f.get("momentum")),
        _qfos_v2_float(f.get("one_tick_momentum")),
        _qfos_v2_float(f.get("signal_strength")),
        str(f.get("symbol_regime") or ""),
        _qfos_v2_float(f.get("symbol_trend_score")),
        _qfos_v2_float(f.get("breakout_score")),
        _qfos_v2_float(f.get("trend_quality")),
        1 if bool(f.get("is_symbol_uptrend")) else 0,
        1 if bool(f.get("is_symbol_downtrend")) else 0,
        1 if bool(f.get("is_choppy")) else 0,
        now_s,
    )

def _qfos_v2_upsert_feature_snapshot(features):
    if not QFOS_POLICY_V2_ENABLED:
        return
    if not isinstance(features, dict):
        return

    import sqlite3
    conn = sqlite3.connect(_qfos_v2_db_path(), timeout=10)
    cur = conn.cursor()

    try:
        _qfos_v2_ensure_feature_table(cur)
        now_s = _qfos_v2_now_local().strftime("%Y-%m-%d %H:%M:%S")

        rows = []
        for symbol, f in features.items():
            if isinstance(f, dict) and f.get("ready", False):
                rows.append(_qfos_v2_feature_to_row(str(symbol), f, now_s))

        cur.executemany("""
            INSERT OR REPLACE INTO symbol_feature_snapshot (
                symbol, price, trend, long_trend, volatility, momentum,
                one_tick_momentum, signal_strength, symbol_regime,
                symbol_trend_score, breakout_score, trend_quality,
                is_symbol_uptrend, is_symbol_downtrend, is_choppy, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        conn.commit()

        if rows:
            print(f"[POLICY_V2] feature_snapshot_upserted count={len(rows)}", flush=True)

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[POLICY_V2] feature_snapshot_error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_v2_latest_feature(symbol):
    import sqlite3
    conn = sqlite3.connect(_qfos_v2_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        _qfos_v2_ensure_feature_table(cur)
        row = cur.execute("""
            SELECT *
            FROM symbol_feature_snapshot
            WHERE symbol = ?
            LIMIT 1
        """, (symbol,)).fetchone()

        if not row:
            return {}

        d = dict(row)
        d["is_symbol_uptrend"] = bool(d.get("is_symbol_uptrend"))
        d["is_symbol_downtrend"] = bool(d.get("is_symbol_downtrend"))
        d["is_choppy"] = bool(d.get("is_choppy"))
        return d

    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_v2_is_fallback_strategy(strategy):
    s = str(strategy or "").lower()
    return "fallback" in s or "scout" in s


def _qfos_v2_market_breadth():
    """
    Reads latest symbol_feature_snapshot and estimates tape health.
    Used to stop isolated pumps from being treated as QUALITY runners
    when most of the market is red.
    """
    import sqlite3
    conn = sqlite3.connect(_qfos_v2_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        _qfos_v2_ensure_feature_table(cur)
        rows = cur.execute("""
            SELECT
                symbol,
                signal_strength,
                breakout_score,
                trend_quality,
                momentum,
                one_tick_momentum,
                symbol_regime,
                is_symbol_uptrend,
                is_symbol_downtrend,
                is_choppy
            FROM symbol_feature_snapshot
        """).fetchall()

        total = 0
        up = 0
        down = 0
        strong_up = 0

        excluded = {"USDC/USDT", "USD1/USDT", "EUR/USDT", "GOLD(PAXG)/USDT", "MOGU/USDT"}

        for r in rows:
            symbol = str(r["symbol"] or "")
            if symbol in excluded:
                continue

            total += 1

            is_up = bool(r["is_symbol_uptrend"])
            is_down = bool(r["is_symbol_downtrend"])
            is_choppy = bool(r["is_choppy"])
            regime = str(r["symbol_regime"] or "")

            signal = _qfos_v2_float(r["signal_strength"])
            breakout = _qfos_v2_float(r["breakout_score"])
            trend_quality = _qfos_v2_float(r["trend_quality"])
            momentum = _qfos_v2_float(r["momentum"])
            one_tick = _qfos_v2_float(r["one_tick_momentum"])

            if is_down:
                down += 1

            if is_up and not is_choppy and regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):
                up += 1

                if (
                    signal >= 0.0065
                    and breakout >= 0.0055
                    and trend_quality > 0
                    and momentum > 0
                    and one_tick >= 0
                ):
                    strong_up += 1

        down_ratio = down / max(total, 1)
        up_ratio = up / max(total, 1)
        strong_up_ratio = strong_up / max(total, 1)

        return {
            "total": total,
            "up": up,
            "down": down,
            "strong_up": strong_up,
            "down_ratio": down_ratio,
            "up_ratio": up_ratio,
            "strong_up_ratio": strong_up_ratio,
        }

    except Exception as exc:
        print(f"[POLICY_V2] breadth_error={exc}", flush=True)
        return {
            "total": 0,
            "up": 0,
            "down": 0,
            "strong_up": 0,
            "down_ratio": 0.0,
            "up_ratio": 0.0,
            "strong_up_ratio": 0.0,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_v2_breadth_allows_quality(symbol, signal, breakout, trend_quality, momentum, one_tick):
    """
    In weak broad tape, only truly exceptional symbols may become runners.
    Otherwise quality-looking names in a red market are treated as scalps.
    """
    b = _qfos_v2_market_breadth()

    bearish_tape = (
        b["total"] >= 20
        and b["down_ratio"] >= 0.45
        and b["strong_up"] <= 3
    )

    if not bearish_tape:
        return True

    exceptional_symbol = (
        signal >= 0.015
        and breakout >= 0.012
        and trend_quality >= 0.010
        and momentum >= 0.002
        and one_tick >= 0.00020
    )

    if exceptional_symbol:
        print(
            f"[POLICY_V2] breadth_allows_exceptional_quality symbol={symbol} "
            f"down_ratio={b['down_ratio']:.2f} strong_up={b['strong_up']} "
            f"signal={signal:.6f} breakout={breakout:.6f} "
            f"trend_quality={trend_quality:.6f} momentum={momentum:.6f} one_tick={one_tick:.6f}",
            flush=True,
        )
        return True

    print(
        f"[POLICY_V2] breadth_blocks_quality symbol={symbol} "
        f"down_ratio={b['down_ratio']:.2f} up_ratio={b['up_ratio']:.2f} "
        f"strong_up={b['strong_up']} total={b['total']} "
        f"signal={signal:.6f} breakout={breakout:.6f} "
        f"trend_quality={trend_quality:.6f} momentum={momentum:.6f} one_tick={one_tick:.6f}",
        flush=True,
    )
    return False


def _qfos_v2_symbol_quality_class(symbol, strategy="", global_regime="SIDEWAYS", exposure_pct=0.0):
    """
    Returns:
      FALLBACK_SCOUT
      SIDEWAYS_SCALP
      QUALITY_TREND_OR_BREAKOUT

    This is the central classifier. Profit Engine V1 should use this.
    """
    if not QFOS_POLICY_V2_ENABLED:
        if str(global_regime).upper() == "SIDEWAYS":
            return "SIDEWAYS_SCALP"
        return "QUALITY_TREND_OR_BREAKOUT"

    f = _qfos_v2_latest_feature(symbol)

    symbol_regime = str(f.get("symbol_regime") or "")
    signal = _qfos_v2_float(f.get("signal_strength"))
    breakout = _qfos_v2_float(f.get("breakout_score"))
    trend_quality = _qfos_v2_float(f.get("trend_quality"))
    momentum = _qfos_v2_float(f.get("momentum"))
    one_tick = _qfos_v2_float(f.get("one_tick_momentum"))
    is_uptrend = bool(f.get("is_symbol_uptrend"))
    is_downtrend = bool(f.get("is_symbol_downtrend"))
    is_choppy = bool(f.get("is_choppy"))

    fallback = _qfos_v2_is_fallback_strategy(strategy)

    if is_downtrend or is_choppy:
        if fallback:
            print(
                f"[POLICY_V2] class FALLBACK_SCOUT symbol={symbol} reason=downtrend_or_choppy "
                f"regime={symbol_regime}",
                flush=True,
            )
            return "FALLBACK_SCOUT"

        if str(global_regime).upper() == "SIDEWAYS":
            print(
                f"[POLICY_V2] class SIDEWAYS_SCALP symbol={symbol} reason=downtrend_or_choppy "
                f"regime={symbol_regime}",
                flush=True,
            )
            return "SIDEWAYS_SCALP"

    # Fallback must be exceptional to upgrade.
    fallback_exceptional = (
        fallback
        and symbol_regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
        and is_uptrend
        and not is_choppy
        and signal >= QFOS_V2_FALLBACK_EXCEPTIONAL_SIGNAL_MIN
        and breakout >= QFOS_V2_FALLBACK_EXCEPTIONAL_BREAKOUT_MIN
        and trend_quality >= QFOS_V2_FALLBACK_EXCEPTIONAL_TREND_QUALITY_MIN
        and momentum >= QFOS_V2_FALLBACK_EXCEPTIONAL_MOMENTUM_MIN
        and one_tick >= QFOS_V2_FALLBACK_EXCEPTIONAL_ONE_TICK_MIN
    )

    if fallback:
        if fallback_exceptional:
            print(
                f"[POLICY_V2] class QUALITY_TREND_OR_BREAKOUT symbol={symbol} reason=exceptional_fallback "
                f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
                f"momentum={momentum:.6f} one_tick={one_tick:.6f}",
                flush=True,
            )
            return "QUALITY_TREND_OR_BREAKOUT"

        print(
            f"[POLICY_V2] class FALLBACK_SCOUT symbol={symbol} reason=non_exceptional_fallback "
            f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
            f"momentum={momentum:.6f} one_tick={one_tick:.6f}",
            flush=True,
        )
        return "FALLBACK_SCOUT"

    normal_quality = (
        symbol_regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
        and is_uptrend
        and not is_choppy
        and signal >= QFOS_V2_QUALITY_SIGNAL_MIN
        and breakout >= QFOS_V2_QUALITY_BREAKOUT_MIN
        and trend_quality >= QFOS_V2_QUALITY_TREND_QUALITY_MIN
        and momentum >= QFOS_V2_QUALITY_MOMENTUM_MIN
        and (
            one_tick >= QFOS_V2_QUALITY_ONE_TICK_MIN
            or (signal >= 0.0080 and momentum >= 0.0015)
        )
    )

    if normal_quality:
        if _qfos_v2_breadth_allows_quality(symbol, signal, breakout, trend_quality, momentum, one_tick):
            print(
                f"[POLICY_V2] class QUALITY_TREND_OR_BREAKOUT symbol={symbol} reason=strong_symbol_quality "
                f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
                f"momentum={momentum:.6f} one_tick={one_tick:.6f} regime={symbol_regime}",
                flush=True,
            )
            return "QUALITY_TREND_OR_BREAKOUT"

        if str(global_regime).upper() == "SIDEWAYS":
            print(
                f"[POLICY_V2] class SIDEWAYS_SCALP symbol={symbol} reason=breadth_blocked_quality "
                f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
                f"momentum={momentum:.6f} one_tick={one_tick:.6f} regime={symbol_regime}",
                flush=True,
            )
            return "SIDEWAYS_SCALP"

    if str(global_regime).upper() == "SIDEWAYS":
        print(
            f"[POLICY_V2] class SIDEWAYS_SCALP symbol={symbol} reason=default_sideways "
            f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
            f"momentum={momentum:.6f} one_tick={one_tick:.6f} regime={symbol_regime}",
            flush=True,
        )
        return "SIDEWAYS_SCALP"

    return "QUALITY_TREND_OR_BREAKOUT"

def _qfos_v2_fallback_order_allowed(order):
    """
    Used before fallback orders reach ORDERS/EXPECTANCY.
    """
    if not QFOS_V2_BLOCK_WEAK_FALLBACK_SOURCE:
        return True

    if not isinstance(order, dict):
        return True

    strategy = str(order.get("strategy") or "")
    if not _qfos_v2_is_fallback_strategy(strategy):
        return True

    symbol = str(order.get("symbol") or "")
    feature = order.get("feature") if isinstance(order.get("feature"), dict) else {}

    symbol_regime = str(feature.get("symbol_regime") or "")
    signal = _qfos_v2_float(order.get("signal_strength", feature.get("signal_strength", 0.0)))
    breakout = _qfos_v2_float(feature.get("breakout_score"))
    trend_quality = _qfos_v2_float(feature.get("trend_quality"))
    momentum = _qfos_v2_float(feature.get("momentum"))
    one_tick = _qfos_v2_float(feature.get("one_tick_momentum"))
    is_uptrend = bool(feature.get("is_symbol_uptrend"))
    is_choppy = bool(feature.get("is_choppy"))

    allowed = (
        symbol_regime in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
        and is_uptrend
        and not is_choppy
        and signal >= QFOS_V2_FALLBACK_EXCEPTIONAL_SIGNAL_MIN
        and breakout >= QFOS_V2_FALLBACK_EXCEPTIONAL_BREAKOUT_MIN
        and trend_quality >= QFOS_V2_FALLBACK_EXCEPTIONAL_TREND_QUALITY_MIN
        and momentum >= QFOS_V2_FALLBACK_EXCEPTIONAL_MOMENTUM_MIN
        and one_tick >= QFOS_V2_FALLBACK_EXCEPTIONAL_ONE_TICK_MIN
    )

    if allowed:
        print(
            f"[POLICY_V2] fallback_allowed_exceptional symbol={symbol} "
            f"signal={signal:.6f} breakout={breakout:.6f} trend_quality={trend_quality:.6f} "
            f"momentum={momentum:.6f} one_tick={one_tick:.6f}",
            flush=True,
        )
    else:
        print(
            f"[POLICY_V2] fallback_blocked_weak symbol={symbol} "
            f"signal={signal:.6f}/{QFOS_V2_FALLBACK_EXCEPTIONAL_SIGNAL_MIN:.6f} "
            f"breakout={breakout:.6f}/{QFOS_V2_FALLBACK_EXCEPTIONAL_BREAKOUT_MIN:.6f} "
            f"trend_quality={trend_quality:.6f}/{QFOS_V2_FALLBACK_EXCEPTIONAL_TREND_QUALITY_MIN:.6f} "
            f"momentum={momentum:.6f}/{QFOS_V2_FALLBACK_EXCEPTIONAL_MOMENTUM_MIN:.6f} "
            f"one_tick={one_tick:.6f}/{QFOS_V2_FALLBACK_EXCEPTIONAL_ONE_TICK_MIN:.6f} "
            f"regime={symbol_regime}",
            flush=True,
        )

    return allowed

def _qfos_v2_filter_fallback_orders(order_list, source="unknown"):
    if not isinstance(order_list, list):
        return order_list

    before = len(order_list)
    kept = []
    for order in order_list:
        try:
            if _qfos_v2_fallback_order_allowed(order):
                kept.append(order)
        except Exception as exc:
            print(f"[POLICY_V2] fallback_filter_error source={source} err={exc}", flush=True)
            kept.append(order)

    removed = before - len(kept)
    if removed:
        print(f"[POLICY_V2] removed_weak_fallback_orders count={removed} source={source}", flush=True)

    return kept


print('[POLICY_V2_FINAL_WIRING_READY] feature_snapshot=result_f_by_symbol fallback_filter=result_orders classifier=profit_engine', flush=True)



# ============================================================
# QFOS RANKED-EVO OPPORTUNITY MODE + TIERED SIZING
# Purpose:
#   The bot became safe but too idle / too small.
#   This patch allows more ranked evo_* trades when SAFE and
#   exposure is low, while keeping fallback scout strict and tiny.
#
# It does NOT loosen Policy V2 fallback filtering.
# It does NOT override quarantine, cooldown, risk, drawdown,
# already-holding, or hard exposure blocks.
# ============================================================

QFOS_OPPORTUNITY_MODE_ENABLED = globals().get("QFOS_OPPORTUNITY_MODE_ENABLED", True)

# Exposure limits for opportunity mode.
QFOS_OPP_MAX_TOTAL_EXPOSURE_PCT_SIDEWAYS = globals().get("QFOS_OPP_MAX_TOTAL_EXPOSURE_PCT_SIDEWAYS", 0.10)
QFOS_OPP_ENTRY_ENABLE_BELOW_EXPOSURE_PCT = globals().get("QFOS_OPP_ENTRY_ENABLE_BELOW_EXPOSURE_PCT", 0.06)

# Tiered sizing.
QFOS_OPP_FALLBACK_SIZE_PCT = globals().get("QFOS_OPP_FALLBACK_SIZE_PCT", 0.0075)
QFOS_OPP_EVO_MEDIUM_SIZE_PCT = globals().get("QFOS_OPP_EVO_MEDIUM_SIZE_PCT", 0.0125)
QFOS_OPP_EVO_HIGH_SIZE_PCT = globals().get("QFOS_OPP_EVO_HIGH_SIZE_PCT", 0.0200)
QFOS_OPP_EVO_EXCEPTIONAL_SIZE_PCT = globals().get("QFOS_OPP_EVO_EXCEPTIONAL_SIZE_PCT", 0.0200)

# Quality thresholds.
QFOS_OPP_MEDIUM_CONFIDENCE = globals().get("QFOS_OPP_MEDIUM_CONFIDENCE", 0.66)
QFOS_OPP_HIGH_CONFIDENCE = globals().get("QFOS_OPP_HIGH_CONFIDENCE", 0.85)

QFOS_OPP_HIGH_SIGNAL = globals().get("QFOS_OPP_HIGH_SIGNAL", 0.0065)
QFOS_OPP_HIGH_BREAKOUT = globals().get("QFOS_OPP_HIGH_BREAKOUT", 0.0055)
QFOS_OPP_HIGH_MOMENTUM = globals().get("QFOS_OPP_HIGH_MOMENTUM", 0.0005)

QFOS_OPP_EXCEPTIONAL_SIGNAL = globals().get("QFOS_OPP_EXCEPTIONAL_SIGNAL", 0.0120)
QFOS_OPP_EXCEPTIONAL_BREAKOUT = globals().get("QFOS_OPP_EXCEPTIONAL_BREAKOUT", 0.0100)
QFOS_OPP_EXCEPTIONAL_MOMENTUM = globals().get("QFOS_OPP_EXCEPTIONAL_MOMENTUM", 0.0015)

def _qfos_opp_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_opp_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_opp_state():
    import sqlite3
    conn = sqlite3.connect(_qfos_opp_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        row = cur.execute("""
            SELECT equity, cash, exposure, drawdown, regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if row:
            equity = _qfos_opp_float(row["equity"], 100.0)
            cash = _qfos_opp_float(row["cash"], equity)
            exposure = _qfos_opp_float(row["exposure"], 0.0)
            drawdown = _qfos_opp_float(row["drawdown"], 0.0)
            regime = str(row["regime"] or "SIDEWAYS")
        else:
            equity, cash, exposure, drawdown, regime = 100.0, 100.0, 0.0, 0.0, "SIDEWAYS"

        return {
            "equity": max(equity, 1.0),
            "cash": max(cash, 0.0),
            "exposure": max(exposure, 0.0),
            "exposure_pct": max(exposure, 0.0) / max(equity, 1.0),
            "drawdown": drawdown,
            "regime": regime,
        }

    except Exception as exc:
        print(f"[OPPORTUNITY_MODE] state_error={exc}", flush=True)
        return {
            "equity": 100.0,
            "cash": 100.0,
            "exposure": 0.0,
            "exposure_pct": 0.0,
            "drawdown": 0.0,
            "regime": "SIDEWAYS",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_opp_is_fallback(strategy):
    s = str(strategy or "").lower()
    return "fallback" in s or "scout" in s

def _qfos_opp_is_evo(strategy):
    return str(strategy or "").lower().startswith("evo_")

def _qfos_opp_get_feature(symbol, fill=None):
    if isinstance(fill, dict) and isinstance(fill.get("feature"), dict):
        return dict(fill.get("feature") or {})

    try:
        if "_qfos_v2_latest_feature" in globals():
            f = _qfos_v2_latest_feature(symbol)
            if isinstance(f, dict):
                return f
    except Exception:
        pass

    return {}

def _qfos_opp_feature_stats(symbol, fill=None):
    f = _qfos_opp_get_feature(symbol, fill)

    return {
        "symbol_regime": str(f.get("symbol_regime") or ""),
        "signal": _qfos_opp_float(f.get("signal_strength")),
        "breakout": _qfos_opp_float(f.get("breakout_score")),
        "trend_quality": _qfos_opp_float(f.get("trend_quality")),
        "momentum": _qfos_opp_float(f.get("momentum")),
        "one_tick": _qfos_opp_float(f.get("one_tick_momentum")),
        "is_uptrend": bool(f.get("is_symbol_uptrend")),
        "is_downtrend": bool(f.get("is_symbol_downtrend")),
        "is_choppy": bool(f.get("is_choppy")),
    }

def _qfos_opp_hard_block_reason(reason):
    r = str(reason or "").lower()

    hard_tokens = [
        "quarantine",
        "quarantined",
        "cooldown",
        "risk",
        "drawdown",
        "blocked",
        "exposure",
        "already_holding",
        "max_trades_per_symbol",
        "price_too_low",
        "downtrend",
        "choppy",
        "cash",
        "insufficient",
        "kill",
        "paused",
    ]

    return any(tok in r for tok in hard_tokens)

def _qfos_opp_quality_tier(symbol, strategy, confidence, fill=None):
    stats = _qfos_opp_feature_stats(symbol, fill)

    if _qfos_opp_is_fallback(strategy):
        return "FALLBACK"

    if not _qfos_opp_is_evo(strategy):
        return "OTHER"

    if stats["is_downtrend"] or stats["is_choppy"]:
        return "BLOCK"

    if confidence < QFOS_OPP_MEDIUM_CONFIDENCE:
        return "WEAK"

    exceptional = (
        confidence >= QFOS_OPP_HIGH_CONFIDENCE
        and stats["symbol_regime"] in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP")
        and stats["is_uptrend"]
        and stats["signal"] >= QFOS_OPP_EXCEPTIONAL_SIGNAL
        and stats["breakout"] >= QFOS_OPP_EXCEPTIONAL_BREAKOUT
        and stats["trend_quality"] > 0
        and stats["momentum"] >= QFOS_OPP_EXCEPTIONAL_MOMENTUM
    )

    if exceptional:
        return "EXCEPTIONAL_EVO"

    high = (
        confidence >= QFOS_OPP_HIGH_CONFIDENCE
        and stats["symbol_regime"] in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP", "SYMBOL_NEUTRAL")
        and not stats["is_downtrend"]
        and stats["signal"] >= QFOS_OPP_HIGH_SIGNAL
        and stats["breakout"] >= QFOS_OPP_HIGH_BREAKOUT
        and stats["momentum"] >= QFOS_OPP_HIGH_MOMENTUM
    )

    if high:
        return "HIGH_EVO"

    return "MEDIUM_EVO"

def _qfos_opp_can_override_entry_reject(symbol, strategy, confidence, reason, fill=None):
    if not QFOS_OPPORTUNITY_MODE_ENABLED:
        return False

    if _qfos_opp_is_fallback(strategy):
        return False

    if not _qfos_opp_is_evo(strategy):
        return False

    if _qfos_opp_hard_block_reason(reason):
        return False

    state = _qfos_opp_state()

    if state["drawdown"] <= -0.015:
        return False

    if state["exposure_pct"] >= QFOS_OPP_ENTRY_ENABLE_BELOW_EXPOSURE_PCT:
        return False

    if str(state["regime"]).upper() == "RISK_OFF":
        return False

    tier = _qfos_opp_quality_tier(symbol, strategy, confidence, fill)

    if tier in ("MEDIUM_EVO", "HIGH_EVO", "EXCEPTIONAL_EVO"):
        print(
            f"[OPPORTUNITY_MODE] override_entry_reject symbol={symbol} "
            f"strategy={strategy} tier={tier} confidence={confidence:.3f} "
            f"old_reason={reason} exposure_pct={state['exposure_pct']:.4f}",
            flush=True,
        )
        return True

    return False

def _qfos_opp_target_size_pct(symbol, strategy, confidence, fill=None):
    tier = _qfos_opp_quality_tier(symbol, strategy, confidence, fill)

    if tier == "FALLBACK":
        return QFOS_OPP_FALLBACK_SIZE_PCT

    if tier == "EXCEPTIONAL_EVO":
        return QFOS_OPP_EVO_EXCEPTIONAL_SIZE_PCT

    if tier == "HIGH_EVO":
        return QFOS_OPP_EVO_HIGH_SIZE_PCT

    if tier == "MEDIUM_EVO":
        return QFOS_OPP_EVO_MEDIUM_SIZE_PCT

    return QFOS_OPP_EVO_MEDIUM_SIZE_PCT

def _qfos_opp_resize_fill(fill, equity=None):
    """
    Resize buy fills by tier. Keeps fallback tiny. Allows ranked evo
    trades to be large enough to make profits meaningful.
    """
    if not QFOS_OPPORTUNITY_MODE_ENABLED:
        return fill

    if not isinstance(fill, dict):
        return fill

    side = str(fill.get("side") or "").lower()
    if side != "buy":
        return fill

    symbol = str(fill.get("symbol") or "")
    strategy = str(fill.get("strategy") or "")
    confidence = _qfos_opp_float(fill.get("confidence"), 0.0)

    price = _qfos_opp_float(fill.get("fill_price", fill.get("expected_price")), 0.0)
    if price <= 0:
        return fill

    state = _qfos_opp_state()
    eq = _qfos_opp_float(equity, state["equity"])
    eq = max(eq, 1.0)

    max_total_exposure_pct = QFOS_OPP_MAX_TOTAL_EXPOSURE_PCT_SIDEWAYS
    if str(state["regime"]).upper() == "TREND":
        max_total_exposure_pct = max(max_total_exposure_pct, 0.15)

    current_exposure = state["exposure"]
    remaining_exposure_capacity = max((eq * max_total_exposure_pct) - current_exposure, 0.0)

    if remaining_exposure_capacity <= 0:
        print(
            f"[OPPORTUNITY_MODE] resize_block_no_capacity symbol={symbol} "
            f"current_exposure={current_exposure:.4f} equity={eq:.4f}",
            flush=True,
        )
        return fill

    target_pct = _qfos_opp_target_size_pct(symbol, strategy, confidence, fill)

    # Fallback must remain tiny even if exceptional.
    if _qfos_opp_is_fallback(strategy):
        target_pct = min(target_pct, QFOS_OPP_FALLBACK_SIZE_PCT)

    target_value = min(eq * target_pct, remaining_exposure_capacity, state["cash"])
    if target_value <= 0:
        return fill

    old_qty = _qfos_opp_float(fill.get("quantity"), 0.0)
    old_value = old_qty * price
    new_qty = target_value / price

    # Do not downsize existing deliberately smaller orders below dust unless fallback.
    if not _qfos_opp_is_fallback(strategy):
        new_qty = max(new_qty, old_qty)

    fill["quantity"] = float(new_qty)
    fill["expected_price"] = price
    fill["fill_price"] = price
    fill["qfos_size_tier"] = _qfos_opp_quality_tier(symbol, strategy, confidence, fill)
    fill["qfos_target_value"] = float(new_qty * price)
    fill["qfos_target_pct"] = float((new_qty * price) / eq)

    print(
        f"[OPPORTUNITY_MODE] resized symbol={symbol} strategy={strategy} "
        f"tier={fill.get('qfos_size_tier')} old_value={old_value:.4f} "
        f"new_value={new_qty * price:.4f} target_pct={fill.get('qfos_target_pct'):.4f} "
        f"confidence={confidence:.3f}",
        flush=True,
    )

    return fill




# ============================================================
# QFOS ALLOCATOR OPPORTUNITY RESCUE
# Purpose:
#   The allocator became too strict: many evo_* candidates are
#   blocked by strict_filter / strategy_threshold before they ever
#   reach Opportunity Mode or execution.
#
# This rescue layer only runs when the normal allocator returns
# zero orders. It does NOT loosen fallback scout. It does NOT
# override quarantine, cooldown, already-holding, downtrend,
# choppy, risk, or exposure hard blocks.
# ============================================================

QFOS_ALLOCATOR_RESCUE_ENABLED = globals().get("QFOS_ALLOCATOR_RESCUE_ENABLED", True)

# Rescue runs only while account is safe and low exposure.
QFOS_ALLOC_RESCUE_ENABLE_BELOW_EXPOSURE_PCT = globals().get("QFOS_ALLOC_RESCUE_ENABLE_BELOW_EXPOSURE_PCT", 0.035)
QFOS_ALLOC_RESCUE_MAX_TOTAL_EXPOSURE_PCT = globals().get("QFOS_ALLOC_RESCUE_MAX_TOTAL_EXPOSURE_PCT", 0.045)
QFOS_ALLOC_RESCUE_MAX_ORDERS_SIDEWAYS = globals().get("QFOS_ALLOC_RESCUE_MAX_ORDERS_SIDEWAYS", 1)
QFOS_ALLOC_RESCUE_MAX_ORDERS_TREND = globals().get("QFOS_ALLOC_RESCUE_MAX_ORDERS_TREND", 2)

# Candidate thresholds.
# These are deliberately looser than the allocator, but still require
# positive signal quality. They mainly relax the "one_tick must be > 0 now"
# problem.
QFOS_ALLOC_RESCUE_MIN_SIGNAL_SIDEWAYS = globals().get("QFOS_ALLOC_RESCUE_MIN_SIGNAL_SIDEWAYS", 0.0020)
QFOS_ALLOC_RESCUE_MIN_SIGNAL_TREND = globals().get("QFOS_ALLOC_RESCUE_MIN_SIGNAL_TREND", 0.0015)

QFOS_ALLOC_RESCUE_MIN_MOMENTUM = globals().get("QFOS_ALLOC_RESCUE_MIN_MOMENTUM", 0.00025)
QFOS_ALLOC_RESCUE_MIN_BREAKOUT = globals().get("QFOS_ALLOC_RESCUE_MIN_BREAKOUT", 0.00080)
QFOS_ALLOC_RESCUE_MIN_TREND = globals().get("QFOS_ALLOC_RESCUE_MIN_TREND", -0.00030)

# one_tick is allowed to be zero/slightly negative if broader quality is strong.
QFOS_ALLOC_RESCUE_MIN_ONE_TICK = globals().get("QFOS_ALLOC_RESCUE_MIN_ONE_TICK", -0.00010)

# Avoid extreme dust, but allow some low-priced MEXC symbols.
QFOS_ALLOC_RESCUE_MIN_PRICE = globals().get("QFOS_ALLOC_RESCUE_MIN_PRICE", 0.02)

# Placeholder size before Opportunity Mode resizes it.
QFOS_ALLOC_RESCUE_PLACEHOLDER_SIZE_PCT = globals().get("QFOS_ALLOC_RESCUE_PLACEHOLDER_SIZE_PCT", 0.0125)

def _qfos_ar_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_ar_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_ar_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_ar_state():
    try:
        if "_qfos_opp_state" in globals():
            return _qfos_opp_state()
    except Exception:
        pass

    import sqlite3
    conn = sqlite3.connect(_qfos_ar_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        row = cur.execute("""
            SELECT equity, cash, exposure, drawdown, regime
            FROM portfolio_snapshots
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if row:
            equity = _qfos_ar_float(row["equity"], 100.0)
            cash = _qfos_ar_float(row["cash"], equity)
            exposure = _qfos_ar_float(row["exposure"], 0.0)
            drawdown = _qfos_ar_float(row["drawdown"], 0.0)
            regime = str(row["regime"] or "SIDEWAYS")
        else:
            equity, cash, exposure, drawdown, regime = 100.0, 100.0, 0.0, 0.0, "SIDEWAYS"

        return {
            "equity": max(equity, 1.0),
            "cash": max(cash, 0.0),
            "exposure": max(exposure, 0.0),
            "exposure_pct": max(exposure, 0.0) / max(equity, 1.0),
            "drawdown": drawdown,
            "regime": regime,
        }
    except Exception as exc:
        print(f"[ALLOCATOR_RESCUE] state_error={exc}", flush=True)
        return {
            "equity": 100.0,
            "cash": 100.0,
            "exposure": 0.0,
            "exposure_pct": 0.0,
            "drawdown": 0.0,
            "regime": "SIDEWAYS",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_ar_is_blocked_symbol(symbol):
    import sqlite3

    conn = sqlite3.connect(_qfos_ar_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        now_s = _qfos_ar_now_local().strftime("%Y-%m-%d %H:%M:%S")

        # Already holding.
        try:
            row = cur.execute("""
                SELECT quantity
                FROM positions
                WHERE symbol = ? AND quantity > 0
                LIMIT 1
            """, (symbol,)).fetchone()
            if row and _qfos_ar_float(row["quantity"]) > 0:
                return True, "already_holding"
        except Exception:
            pass

        # Symbol quarantine.
        try:
            row = cur.execute("""
                SELECT reason, blocked_until
                FROM symbol_quarantine
                WHERE symbol = ?
                LIMIT 1
            """, (symbol,)).fetchone()

            if row:
                blocked_until = str(row["blocked_until"] or "")
                if not blocked_until or blocked_until >= now_s:
                    return True, f"symbol_quarantined:{row['reason']}"
        except Exception:
            pass

        return False, ""

    except Exception as exc:
        return True, f"block_check_error:{exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_ar_pick_strategy(local_vars):
    names = [
        "top_strategy",
        "best_strategy",
        "strategy",
        "selected_strategy",
        "allocator_strategy",
    ]

    for name in names:
        v = local_vars.get(name)
        if v and str(v).lower().startswith("evo_"):
            return str(v)

    # Try strategy_scores in locals.
    try:
        scores = local_vars.get("strategy_scores")
        if isinstance(scores, list) and scores:
            for row in scores:
                if isinstance(row, dict):
                    s = str(row.get("strategy") or "")
                    if s.lower().startswith("evo_"):
                        return s
    except Exception:
        pass

    # Fallback still starts with evo_ so Opportunity Mode treats it as evo path.
    return "evo_allocator_rescue"

def _qfos_ar_candidate_score(symbol, f):
    signal = _qfos_ar_float(f.get("signal_strength"))
    breakout = _qfos_ar_float(f.get("breakout_score"))
    trend_quality = _qfos_ar_float(f.get("trend_quality"))
    momentum = _qfos_ar_float(f.get("momentum"))
    one_tick = _qfos_ar_float(f.get("one_tick_momentum"))
    trend = _qfos_ar_float(f.get("trend"))
    volatility = abs(_qfos_ar_float(f.get("volatility")))

    score = (
        signal * 1.80
        + breakout * 1.20
        + max(momentum, 0.0) * 1.50
        + max(trend, 0.0) * 0.80
        + max(one_tick, 0.0) * 0.80
        + max(trend_quality, 0.0) * 1.00
        - volatility * 0.20
    )

    return score

def _qfos_ar_feature_ok(symbol, f, regime):
    if not isinstance(f, dict):
        return False, "bad_feature"

    if not f.get("ready", False):
        return False, "not_ready"

    price = _qfos_ar_float(f.get("price"))
    trend = _qfos_ar_float(f.get("trend"))
    momentum = _qfos_ar_float(f.get("momentum"))
    one_tick = _qfos_ar_float(f.get("one_tick_momentum"))
    signal = _qfos_ar_float(f.get("signal_strength"))
    breakout = _qfos_ar_float(f.get("breakout_score"))
    trend_quality = _qfos_ar_float(f.get("trend_quality"))
    symbol_regime = str(f.get("symbol_regime") or "")

    is_downtrend = bool(f.get("is_symbol_downtrend"))
    is_choppy = bool(f.get("is_choppy"))

    if price < QFOS_ALLOC_RESCUE_MIN_PRICE:
        return False, f"price_too_low:{price}"

    if is_downtrend:
        return False, "symbol_downtrend"

    if is_choppy:
        return False, "symbol_choppy"

    if trend < QFOS_ALLOC_RESCUE_MIN_TREND:
        return False, f"trend_too_negative:{trend:.6f}"

    if momentum < QFOS_ALLOC_RESCUE_MIN_MOMENTUM:
        return False, f"momentum_too_low:{momentum:.6f}"

    if one_tick < QFOS_ALLOC_RESCUE_MIN_ONE_TICK:
        # one_tick can be zero, but not meaningfully negative.
        return False, f"one_tick_too_negative:{one_tick:.6f}"

    min_signal = QFOS_ALLOC_RESCUE_MIN_SIGNAL_SIDEWAYS
    if str(regime).upper() == "TREND":
        min_signal = QFOS_ALLOC_RESCUE_MIN_SIGNAL_TREND

    if signal < min_signal:
        return False, f"signal_too_low:{signal:.6f}"

    if breakout < QFOS_ALLOC_RESCUE_MIN_BREAKOUT:
        # Allow high signal even if breakout calculation lags.
        if not (signal >= min_signal * 1.75 and trend_quality > 0):
            return False, f"breakout_too_low:{breakout:.6f}"

    # If global market is SIDEWAYS, prefer actual symbol strength.
    if str(regime).upper() == "SIDEWAYS":
        if symbol_regime not in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP", "SYMBOL_NEUTRAL"):
            return False, f"bad_symbol_regime:{symbol_regime}"

    return True, "ok"

def _qfos_allocator_opportunity_rescue(result, local_vars=None):
    """
    Creates ranked evo buy orders only when normal allocator returned zero orders.
    This is the upstream counterpart to Opportunity Mode tiered sizing.
    """
    if not QFOS_ALLOCATOR_RESCUE_ENABLED:
        return []

    local_vars = local_vars or {}

    if not isinstance(result, dict):
        return []

    existing = result.get("orders") or []
    if isinstance(existing, list) and len(existing) > 0:
        return existing

    f_by_symbol = result.get("f_by_symbol") or result.get("features") or local_vars.get("features") or local_vars.get("f_by_symbol") or {}
    if not isinstance(f_by_symbol, dict) or not f_by_symbol:
        print("[ALLOCATOR_RESCUE] skipped reason=no_feature_map", flush=True)
        return []

    state = _qfos_ar_state()
    regime = str(state.get("regime") or "SIDEWAYS").upper()

    _pacing_ok, _pacing_reason = _qfos_harden_rescue_pacing_allows(regime)
    if not _pacing_ok:
        print(f"[ALLOCATOR_RESCUE] skipped reason={_pacing_reason}", flush=True)
        return []

    if state["exposure_pct"] >= QFOS_RESCUE_ENABLE_BELOW_EXPOSURE_PCT_HARD:
        print(f"[ALLOCATOR_RESCUE] skipped reason=hard_exposure_gate exposure_pct={state['exposure_pct']:.4f}", flush=True)
        return []

    if regime == "RISK_OFF":
        print("[ALLOCATOR_RESCUE] skipped reason=risk_off", flush=True)
        return []

    if state["drawdown"] <= -0.015:
        print(f"[ALLOCATOR_RESCUE] skipped reason=drawdown drawdown={state['drawdown']:.4f}", flush=True)
        return []

    if state["exposure_pct"] >= QFOS_ALLOC_RESCUE_ENABLE_BELOW_EXPOSURE_PCT:
        print(f"[ALLOCATOR_RESCUE] skipped reason=exposure exposure_pct={state['exposure_pct']:.4f}", flush=True)
        return []

    candidates = []

    for symbol, f in f_by_symbol.items():
        symbol = str(symbol)

        blocked, block_reason = _qfos_ar_is_blocked_symbol(symbol)
        if blocked:
            continue

        ok, reason = _qfos_ar_feature_ok(symbol, f, regime)
        if not ok:
            continue

        score = _qfos_ar_candidate_score(symbol, f)

        if score <= 0:
            continue

        candidates.append((score, symbol, f))

    candidates.sort(reverse=True, key=lambda x: x[0])

    max_orders = QFOS_ALLOC_RESCUE_MAX_ORDERS_SIDEWAYS
    if regime == "TREND":
        max_orders = QFOS_ALLOC_RESCUE_MAX_ORDERS_TREND

    strategy = _qfos_ar_pick_strategy(local_vars)

    orders = []
    remaining_capacity = max(
        (state["equity"] * QFOS_ALLOC_RESCUE_MAX_TOTAL_EXPOSURE_PCT) - state["exposure"],
        0.0,
    )

    for score, symbol, f in candidates[:max_orders]:
        price = _qfos_ar_float(f.get("price"))
        if price <= 0:
            continue

        target_value = min(
            state["equity"] * QFOS_ALLOC_RESCUE_PLACEHOLDER_SIZE_PCT,
            remaining_capacity,
            state["cash"],
        )

        if target_value <= 0:
            break

        qty = target_value / price
        confidence = min(0.95, max(0.66, 0.66 + score * 12.0))

        order = {
            "symbol": symbol,
            "side": "buy",
            "quantity": float(qty),
            "expected_price": float(price),
            "fill_price": float(price),
            "confidence": float(confidence),
            "strategy": strategy,
            "signal_strength": float(_qfos_ar_float(f.get("signal_strength"))),
            "feature": dict(f),
            "source": "allocator_opportunity_rescue",
            "qfos_allocator_rescue_score": float(score),
            "slippage_bps": 0,
            "shadow_mode": False,
        }

        orders.append(order)
        remaining_capacity -= target_value

        print(
            f"[ALLOCATOR_RESCUE] selected symbol={symbol} strategy={strategy} "
            f"score={score:.6f} signal={_qfos_ar_float(f.get('signal_strength')):.6f} "
            f"breakout={_qfos_ar_float(f.get('breakout_score')):.6f} "
            f"trend={_qfos_ar_float(f.get('trend')):.6f} "
            f"momentum={_qfos_ar_float(f.get('momentum')):.6f} "
            f"one_tick={_qfos_ar_float(f.get('one_tick_momentum')):.6f} "
            f"value={target_value:.4f} confidence={confidence:.3f}",
            flush=True,
        )

    if orders:
        print(f"[ALLOCATOR_RESCUE] injected_orders count={len(orders)}", flush=True)
    else:
        print("[ALLOCATOR_RESCUE] no_candidate_passed", flush=True)

    return orders




# ============================================================
# QFOS FILL DEFAULT NORMALIZER
# Purpose:
#   Rescue-generated orders can miss fields that the DB trade insert
#   requires, especially slippage_bps. This normalizes every fill before
#   can_buy/apply_buy/DB write.
# ============================================================

def _qfos_fill_default_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_normalize_fill_defaults(fill):
    if not isinstance(fill, dict):
        return fill

    fill.setdefault("slippage_bps", 0)
    fill.setdefault("shadow_mode", False)
    fill.setdefault("live", False)

    if "side" not in fill or not fill.get("side"):
        fill["side"] = "buy"

    if "confidence" not in fill or fill.get("confidence") is None:
        fill["confidence"] = 0.66

    price = _qfos_fill_default_float(fill.get("fill_price", fill.get("expected_price")), 0.0)
    if price > 0:
        fill["fill_price"] = price
        fill["expected_price"] = _qfos_fill_default_float(fill.get("expected_price"), price)

    qty = _qfos_fill_default_float(fill.get("quantity"), 0.0)
    if qty > 0:
        fill["quantity"] = qty

    if "strategy" not in fill or not fill.get("strategy"):
        fill["strategy"] = "unknown_strategy"

    return fill

def _qfos_normalize_fill_list(fills):
    if not isinstance(fills, list):
        return []
    return [_qfos_normalize_fill_defaults(f) for f in fills if isinstance(f, dict)]




# ============================================================
# QFOS RESCUE HARDENING + DUPLICATE SELL GUARD
# Purpose:
#   Allocator rescue is now working, but it must not:
#   - cluster 3 rescue entries within minutes,
#   - oversize ultra-low-price/high-volatility coins,
#   - double-sell a position after another exit already closed it.
# ============================================================

QFOS_RESCUE_HARDENING_ENABLED = globals().get("QFOS_RESCUE_HARDENING_ENABLED", True)

# Rescue pacing.
QFOS_RESCUE_MIN_SECONDS_BETWEEN_SIDEWAYS = globals().get("QFOS_RESCUE_MIN_SECONDS_BETWEEN_SIDEWAYS", 900)  # 15 minutes
QFOS_RESCUE_MAX_RESCUE_BUYS_PER_HOUR_SIDEWAYS = globals().get("QFOS_RESCUE_MAX_RESCUE_BUYS_PER_HOUR_SIDEWAYS", 2)

# Tighter rescue exposure. Opportunity mode can still resize, but rescue should not
# keep adding quickly in SIDEWAYS.
QFOS_RESCUE_ENABLE_BELOW_EXPOSURE_PCT_HARD = globals().get("QFOS_RESCUE_ENABLE_BELOW_EXPOSURE_PCT_HARD", 0.035)
QFOS_RESCUE_MAX_TOTAL_EXPOSURE_PCT_HARD = globals().get("QFOS_RESCUE_MAX_TOTAL_EXPOSURE_PCT_HARD", 0.045)

# Block ultra-cheap/high-volatility rescue names like SENS-style moves.
QFOS_RESCUE_MIN_PRICE_HARD = globals().get("QFOS_RESCUE_MIN_PRICE_HARD", 0.02)
QFOS_RESCUE_MAX_VOLATILITY_HARD = globals().get("QFOS_RESCUE_MAX_VOLATILITY_HARD", 0.012)
QFOS_RESCUE_LOW_PRICE_CUTOFF = globals().get("QFOS_RESCUE_LOW_PRICE_CUTOFF", 0.10)
QFOS_RESCUE_LOW_PRICE_MAX_VOLATILITY = globals().get("QFOS_RESCUE_LOW_PRICE_MAX_VOLATILITY", 0.006)

# Reduce sizing after the SENS failure.
QFOS_OPP_EVO_MEDIUM_SIZE_PCT = globals().get("QFOS_OPP_EVO_MEDIUM_SIZE_PCT", 0.0125)
QFOS_OPP_EVO_HIGH_SIZE_PCT = globals().get("QFOS_OPP_EVO_HIGH_SIZE_PCT", 0.0200)
QFOS_OPP_EVO_EXCEPTIONAL_SIZE_PCT = globals().get("QFOS_OPP_EVO_EXCEPTIONAL_SIZE_PCT", 0.0200)

def _qfos_harden_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_harden_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_harden_recent_rescue_buy_count(seconds=3600):
    import sqlite3
    from datetime import datetime, timedelta

    conn = sqlite3.connect(_qfos_harden_db_path(), timeout=10)
    cur = conn.cursor()

    try:
        cutoff = (datetime.utcnow() + timedelta(hours=3) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")

        row = cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE side = 'buy'
              AND strategy = 'evo_allocator_rescue'
              AND created_at >= ?
        """, (cutoff,)).fetchone()

        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_harden_seconds_since_last_rescue_buy():
    import sqlite3
    from datetime import datetime, timedelta

    conn = sqlite3.connect(_qfos_harden_db_path(), timeout=10)
    cur = conn.cursor()

    try:
        row = cur.execute("""
            SELECT created_at
            FROM trades
            WHERE side = 'buy'
              AND strategy = 'evo_allocator_rescue'
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if not row or not row[0]:
            return 999999

        s = str(row[0]).replace("T", " ").replace("Z", "").split(".")[0]
        last = datetime.fromisoformat(s)
        now = datetime.utcnow() + timedelta(hours=3)
        return max((now - last).total_seconds(), 0)
    except Exception:
        return 999999
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_harden_rescue_pacing_allows(regime="SIDEWAYS"):
    if not QFOS_RESCUE_HARDENING_ENABLED:
        return True, "disabled"

    if str(regime).upper() != "SIDEWAYS":
        return True, "not_sideways"

    recent = _qfos_harden_recent_rescue_buy_count(3600)
    since_last = _qfos_harden_seconds_since_last_rescue_buy()

    if recent >= QFOS_RESCUE_MAX_RESCUE_BUYS_PER_HOUR_SIDEWAYS:
        return False, f"rescue_hourly_cap recent={recent}"

    if since_last < QFOS_RESCUE_MIN_SECONDS_BETWEEN_SIDEWAYS:
        return False, f"rescue_spacing since_last={since_last:.0f}s"

    return True, "ok"

def _qfos_harden_feature_allows_rescue(symbol, f):
    if not QFOS_RESCUE_HARDENING_ENABLED:
        return True, "disabled"

    price = _qfos_harden_float(f.get("price"))
    volatility = abs(_qfos_harden_float(f.get("volatility")))

    if price < QFOS_RESCUE_MIN_PRICE_HARD:
        return False, f"hard_price_too_low price={price}"

    if volatility > QFOS_RESCUE_MAX_VOLATILITY_HARD:
        return False, f"hard_volatility_too_high volatility={volatility:.6f}"

    if price < QFOS_RESCUE_LOW_PRICE_CUTOFF and volatility > QFOS_RESCUE_LOW_PRICE_MAX_VOLATILITY:
        return False, f"low_price_high_volatility price={price} volatility={volatility:.6f}"

    return True, "ok"

def _qfos_harden_open_position_qty(symbol):
    import sqlite3

    conn = sqlite3.connect(_qfos_harden_db_path(), timeout=10)
    cur = conn.cursor()

    try:
        row = cur.execute("""
            SELECT quantity
            FROM positions
            WHERE symbol = ?
            LIMIT 1
        """, (symbol,)).fetchone()

        if not row:
            return 0.0

        return max(_qfos_harden_float(row[0]), 0.0)
    except Exception:
        return 0.0
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_sell_guard_cap_fill(fill):
    """
    Prevent duplicate sells and cap sell quantity to actual open DB quantity.
    This protects against two exit systems selling the same position.
    """
    if not isinstance(fill, dict):
        return False, "bad_fill"

    side = str(fill.get("side") or "").lower()
    if side != "sell":
        return True, "not_sell"

    symbol = str(fill.get("symbol") or "")
    requested_qty = abs(_qfos_harden_float(fill.get("quantity")))

    open_qty = _qfos_harden_open_position_qty(symbol)

    if open_qty <= 1e-12:
        return False, "duplicate_sell_no_open_position"

    if requested_qty <= 0:
        return False, "sell_qty_zero"

    if requested_qty > open_qty:
        print(
            f"[SELL_GUARD] capped_sell_qty symbol={symbol} requested={requested_qty:.8f} open={open_qty:.8f}",
            flush=True,
        )
        fill["quantity"] = float(open_qty)

    return True, "ok"




# ============================================================
# QFOS FULL-PROFIT EXIT MODE
# Purpose:
#   Small accounts should not split tiny $1-$3 positions into
#   partial take-profit + runner fragments. Close full position
#   on TP, then look for the next trade.
# ============================================================

QFOS_FULL_PROFIT_EXIT_MODE = globals().get("QFOS_FULL_PROFIT_EXIT_MODE", True)

def _qfos_full_exit_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def _qfos_full_exit_db_path():
    from pathlib import Path
    for p in [Path("/app/data/quant.db"), Path("data/quant.db"), Path("./data/quant.db"), Path("quant.db")]:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return "data/quant.db"

def _qfos_full_exit_open_qty(symbol):
    import sqlite3
    conn = sqlite3.connect(_qfos_full_exit_db_path(), timeout=10)
    cur = conn.cursor()
    try:
        row = cur.execute("""
            SELECT quantity
            FROM positions
            WHERE symbol = ?
            LIMIT 1
        """, (symbol,)).fetchone()
        if not row:
            return 0.0
        return max(_qfos_full_exit_float(row[0]), 0.0)
    except Exception:
        return 0.0
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _qfos_full_exit_normalize_sell(fill):
    """
    If a take-profit sell is generated, sell the full open quantity.
    If no open position exists, reject duplicate/stale sell.
    """
    if not QFOS_FULL_PROFIT_EXIT_MODE:
        return fill

    if not isinstance(fill, dict):
        return fill

    side = str(fill.get("side") or "").lower()
    if side != "sell":
        return fill

    symbol = str(fill.get("symbol") or "")
    reason = str(fill.get("strategy") or "")

    open_qty = _qfos_full_exit_open_qty(symbol)
    if open_qty <= 1e-12:
        fill["qfos_reject_sell"] = True
        fill["qfos_reject_reason"] = "duplicate_or_stale_sell_no_open_position"
        print(
            f"[FULL_PROFIT_MODE] reject_sell_no_open_position symbol={symbol} reason={reason}",
            flush=True,
        )
        return fill

    take_profit_like = (
        "take_profit" in reason
        or "trailing_profit" in reason
        or "runner_trailing" in reason
        or "green_to_red" in reason
    )

    if take_profit_like:
        old_qty = _qfos_full_exit_float(fill.get("quantity"))
        fill["quantity"] = float(open_qty)
        fill["qfos_full_profit_mode"] = True
        print(
            f"[FULL_PROFIT_MODE] full_exit symbol={symbol} reason={reason} "
            f"old_qty={old_qty:.8f} full_qty={open_qty:.8f}",
            flush=True,
        )
        return fill

    # For stops, cap to open quantity so duplicate sell cannot oversell.
    old_qty = _qfos_full_exit_float(fill.get("quantity"))
    if old_qty > open_qty:
        fill["quantity"] = float(open_qty)
        print(
            f"[FULL_PROFIT_MODE] capped_stop_sell symbol={symbol} reason={reason} "
            f"old_qty={old_qty:.8f} full_qty={open_qty:.8f}",
            flush=True,
        )

    return fill

def _qfos_full_exit_filter_fills(fills):
    if not isinstance(fills, list):
        return []
    out = []
    for f in fills:
        if not isinstance(f, dict):
            continue
        f = _qfos_full_exit_normalize_sell(f)
        if f.get("qfos_reject_sell"):
            continue
        out.append(f)
    return out


if __name__ == '__main__':
    main()

# QFOS_PROFIT_ENGINE_SIDEWAYS_GUARD_V1 installed

# METRIC_TRUTH_STOPLOSS_WINRATE_V1 installed
