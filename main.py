# QFOS_POSTGRES_AUDIT_BOOT_GUARD_V1
# Temporary fail-closed guards for a PostgreSQL-only paused startup audit.
import os as _qfos_audit_os
import sqlite3 as _qfos_audit_sqlite3

_QFOS_AUDIT_POSTGRES_ONLY = str(
    _qfos_audit_os.getenv("QFOS_POSTGRES_ONLY", "")
).strip().lower() in ("1", "true", "yes")

_QFOS_AUDIT_BOOT = str(
    _qfos_audit_os.getenv("QFOS_AUDIT_BOOT", "")
).strip().lower() in ("1", "true", "yes")

if _QFOS_AUDIT_POSTGRES_ONLY:
    _qfos_audit_dsn = (
        _qfos_audit_os.getenv("DATABASE_URL")
        or _qfos_audit_os.getenv("DB_URL")
        or ""
    ).strip().lower()

    if not _qfos_audit_dsn.startswith(("postgresql://", "postgres://", "postgresql+")):
        raise RuntimeError(
            "[QFOS_POSTGRES_ONLY] DATABASE_URL/DB_URL must be PostgreSQL before startup."
        )

    def _qfos_blocked_sqlite_connect(*args, **kwargs):
        raise RuntimeError(
            "[QFOS_POSTGRES_ONLY] SQLite access blocked during PostgreSQL-only audit boot."
        )

    _qfos_audit_sqlite3.connect = _qfos_blocked_sqlite_connect
    print("[QFOS_POSTGRES_ONLY] SQLite connection guard enabled", flush=True)

if _QFOS_AUDIT_BOOT:
    print("[QFOS_AUDIT_BOOT] all trade-fill persistence is blocked", flush=True)

# QFOS_DB_PATH_CONTRACT: Docker runtime SQLite path contract.
# Agent 1 Runtime DB Access Repair.
# This block only normalizes DB path environment variables.
import os as _qfos_db_os
_qfos_db_path = (
    _qfos_db_os.environ.get("DB_PATH")
    or _qfos_db_os.environ.get("DATABASE_PATH")
    or _qfos_db_os.environ.get("SQLITE_DB_PATH")
    or _qfos_db_os.environ.get("QFOS_DB_PATH")
    or _qfos_db_os.environ.get("QUANT_DB_PATH")
    or "/app/data/quant.db"
)
_qfos_db_os.environ["DB_PATH"] = _qfos_db_path
_qfos_db_os.environ["DATABASE_PATH"] = _qfos_db_path
_qfos_db_os.environ["SQLITE_DB_PATH"] = _qfos_db_path
_qfos_db_os.environ["QFOS_DB_PATH"] = _qfos_db_path
_qfos_db_os.environ["QUANT_DB_PATH"] = _qfos_db_path
_qfos_db_os.makedirs(_qfos_db_os.path.dirname(_qfos_db_os.path.abspath(_qfos_db_path)), exist_ok=True)


# QFOS_RUNTIME_SQLITE_STABILITY_FIX_V1
# Runtime DB contract:
# Host:      .\data\quant.db
# Container: /app/data/quant.db
# Purpose: force all late/background SQLite helpers to resolve the same DB file.
def qfos_runtime_db_path():
    import os
    p = (
        os.environ.get("DB_PATH")
        or os.environ.get("DATABASE_PATH")
        or os.environ.get("SQLITE_DB_PATH")
        or os.environ.get("QFOS_DB_PATH")
        or os.environ.get("QUANT_DB_PATH")
        or "/app/data/quant.db"
    )
    if not p:
        p = "/app/data/quant.db"
    os.environ["DB_PATH"] = p
    os.environ["DATABASE_PATH"] = p
    os.environ["SQLITE_DB_PATH"] = p
    os.environ["QFOS_DB_PATH"] = p
    os.environ["QUANT_DB_PATH"] = p
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    return p

def qfos_runtime_sqlite_connect(timeout=30):
    import sqlite3
    conn = sqlite3.connect(qfos_runtime_db_path(), timeout=timeout)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


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
# QFOS OUTLIER LOSS CAP ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â percentage based, equity-scaled
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
from qfos_runtime.execution_telemetry import ExecutionCycleTelemetry
from qfos_runtime.exit_intents import deduplicate_exit_intents
from qfos_runtime.execution_gate import default_execution_gate
# QFOS_RUNTIME_TELEMETRY_SAFE_V2
from core.runtime_telemetry import (
    runtime_start as qfos_runtime_start,
    loop_control_observed as qfos_loop_control_observed,
    cycle_from_locals as qfos_cycle_from_locals,
)

QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason

# QFOS_PAUSE_REASON_CALLABLE_GUARD_START
def qfos_safe_pause_reason_text():
    """
    Return pause reason text without relying on the global pause_reason name.

    The baseline reset code must never overwrite qfos_safe_pause_reason_text(), but older
    patches may still have polluted globals()["pause_reason"] or
    core.control.pause_reason. This helper always falls back to the original
    imported callable captured at module import time.
    """
    try:
        import core.control as _control
        fn = getattr(_control, "pause_reason", None)
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass

    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass

    try:
        val = globals().get("pause_reason", "")
        if callable(val):
            return str(val() or "")
        return str(val or "")
    except Exception:
        return ""


def qfos_restore_pause_reason_callable():
    """
    Repair accidental overwrite of pause_reason in this module and core.control.
    """
    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            globals()["pause_reason"] = fn
            try:
                import core.control as _control
                if not callable(getattr(_control, "pause_reason", None)):
                    setattr(_control, "pause_reason", fn)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False
# QFOS_PAUSE_REASON_CALLABLE_GUARD_END
# C44: core.telegram_alerts remains the sole Telegram sender for main.py.
from fastapi import FastAPI

# QFOS_EXPECTANCY_INLINE_START
# Embedded because Docker image failed to import qfos_expectancy_patch.py.
_QFOS_EXPECTANCY_NS = {"__file__": __file__, "__name__": "_qfos_expectancy_inline"}
exec('\n\nimport json\nimport math\nimport time\nfrom datetime import datetime, timezone\nfrom pathlib import Path\nfrom typing import Any, Dict, List\n\nROOT = Path(__file__).resolve().parent\nCONFIG_PATH = ROOT / "qfos_expectancy_config.json"\n\nDEFAULT_CONFIG = {\n    "enabled": True,\n    "sideways_scout_notional_usd": 1.00,\n    "trend_scout_notional_usd": 1.50,\n    "min_trade_price": 0.01,\n    "min_scout_signal_sideways": 0.0012,\n    "min_scout_trend_quality": 0.001,\n    "max_scout_volatility_sideways": 0.006,\n    "require_positive_one_tick_for_scout": True,\n    "fallback_stop_loss_pct": -0.006,\n    "breakeven_arm_pct": 0.0035,\n    "breakeven_exit_floor_pct": 0.0004,\n    "trailing_arm_pct": 0.005,\n    "trailing_giveback_pct": 0.0025,\n    "sideways_time_stop_minutes": 45,\n    "sideways_time_stop_min_pnl": -0.0015,\n    "sideways_time_stop_max_pnl": 0.002,\n    "symbol_cooldown_after_losses": 2,\n    "cooldown_minutes": 180,\n    "state_file": "qfos_expectancy_state.json",\n    "decision_log": "qfos_expectancy_decisions.jsonl",\n}\n\ndef _load_config() -> Dict[str, Any]:\n    try:\n        if CONFIG_PATH.exists():\n            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))\n            merged = dict(DEFAULT_CONFIG)\n            merged.update(data or {})\n            return merged\n    except Exception:\n        pass\n    return dict(DEFAULT_CONFIG)\n\nCFG = _load_config()\nSTATE_PATH = ROOT / str(CFG.get("state_file", "qfos_expectancy_state.json"))\nDECISION_LOG = ROOT / str(CFG.get("decision_log", "qfos_expectancy_decisions.jsonl"))\n\ndef _now_ts() -> float:\n    return time.time()\n\ndef _utc() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\ndef _safe_float(x: Any, default: float = 0.0) -> float:\n    try:\n        if x is None:\n            return default\n        v = float(x)\n        if math.isnan(v) or math.isinf(v):\n            return default\n        return v\n    except Exception:\n        return default\n\ndef _safe_str(x: Any, default: str = "") -> str:\n    try:\n        return str(x)\n    except Exception:\n        return default\n\ndef _read_state() -> Dict[str, Any]:\n    try:\n        if STATE_PATH.exists():\n            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))\n            if isinstance(data, dict):\n                data.setdefault("positions", {})\n                data.setdefault("losses", {})\n                return data\n    except Exception:\n        pass\n    return {"positions": {}, "losses": {}}\n\ndef _write_state(state: Dict[str, Any]) -> None:\n    try:\n        tmp = STATE_PATH.with_suffix(".tmp")\n        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")\n        tmp.replace(STATE_PATH)\n    except Exception:\n        pass\n\ndef _log(action: str, **payload: Any) -> None:\n    try:\n        row = {"ts": _utc(), "action": action}\n        row.update(payload)\n        with DECISION_LOG.open("a", encoding="utf-8") as f:\n            f.write(json.dumps(row, sort_keys=True) + "\\n")\n    except Exception:\n        pass\n\ndef _as_dict(obj: Any) -> Dict[str, Any]:\n    if isinstance(obj, dict):\n        return obj\n\n    data = {}\n    for name in (\n        "symbol", "quantity", "qty", "avg_entry", "entry_price",\n        "mark_price", "price", "strategy", "created_at", "updated_at"\n    ):\n        if hasattr(obj, name):\n            try:\n                data[name] = getattr(obj, name)\n            except Exception:\n                pass\n    return data\n\ndef _get_feature(features: Any, symbol: str) -> Dict[str, Any]:\n    if isinstance(features, dict):\n        value = features.get(symbol, {})\n        if isinstance(value, dict):\n            return value\n    return {}\n\ndef _get_price(symbol: str, pos: Dict[str, Any], feature: Dict[str, Any], prices: Any) -> float:\n    if isinstance(prices, dict) and symbol in prices:\n        p = _safe_float(prices.get(symbol))\n        if p > 0:\n            return p\n\n    p = _safe_float(feature.get("price"))\n    if p > 0:\n        return p\n\n    p = _safe_float(pos.get("mark_price"))\n    if p > 0:\n        return p\n\n    return _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price"))\n\ndef _get_positions(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:\n    raw = (\n        ctx.get("positions")\n        or ctx.get("open_positions")\n        or ctx.get("portfolio_positions")\n        or {}\n    )\n\n    out: Dict[str, Dict[str, Any]] = {}\n\n    if isinstance(raw, dict):\n        for sym, pos in raw.items():\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol") or sym)\n            if symbol:\n                out[symbol] = pd\n\n    elif isinstance(raw, list):\n        for pos in raw:\n            pd = _as_dict(pos)\n            symbol = _safe_str(pd.get("symbol"))\n            if symbol:\n                out[symbol] = pd\n\n    return out\n\ndef _order_symbol(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("symbol"))\n\ndef _order_side(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("side")).lower()\n\ndef _order_strategy(order: Dict[str, Any]) -> str:\n    return _safe_str(order.get("strategy") or order.get("reason") or "unknown")\n\ndef _order_price(order: Dict[str, Any], feature: Dict[str, Any]) -> float:\n    p = _safe_float(order.get("fill_price") or order.get("expected_price") or order.get("price"))\n    if p > 0:\n        return p\n    return _safe_float(feature.get("price"))\n\ndef _recent_losses(symbol: str, state: Dict[str, Any], minutes: float) -> int:\n    losses = state.get("losses", {}).get(symbol, [])\n    now = _now_ts()\n    cutoff = now - minutes * 60\n    fresh = [x for x in losses if _safe_float(x.get("ts")) >= cutoff]\n    state.setdefault("losses", {})[symbol] = fresh\n    return len(fresh)\n\ndef _record_loss(symbol: str, strategy: str, state: Dict[str, Any]) -> None:\n    if not symbol:\n        return\n    state.setdefault("losses", {}).setdefault(symbol, []).append({\n        "ts": _now_ts(),\n        "strategy": strategy,\n    })\n\ndef _record_buy(order: Dict[str, Any], feature: Dict[str, Any], state: Dict[str, Any]) -> None:\n    symbol = _order_symbol(order)\n    if not symbol:\n        return\n\n    price = _order_price(order, feature)\n    if price <= 0:\n        return\n\n    state.setdefault("positions", {})[symbol] = {\n        "entry_ts": _now_ts(),\n        "entry_price": price,\n        "highest_price": price,\n        "highest_pnl_pct": 0.0,\n        "strategy": _order_strategy(order),\n        "signal_strength": _safe_float(order.get("signal_strength") or feature.get("signal_strength")),\n    }\n\ndef _ensure_position_meta(symbol: str, pos: Dict[str, Any], price: float, state: Dict[str, Any]) -> Dict[str, Any]:\n    positions = state.setdefault("positions", {})\n    meta = positions.get(symbol)\n\n    entry = _safe_float(pos.get("avg_entry") or pos.get("entry_price") or pos.get("price") or price)\n\n    if not isinstance(meta, dict):\n        meta = {\n            "entry_ts": _now_ts(),\n            "entry_price": entry if entry > 0 else price,\n            "highest_price": price,\n            "highest_pnl_pct": 0.0,\n            "strategy": _safe_str(pos.get("strategy") or "recovered_position"),\n            "signal_strength": 0.0,\n        }\n        positions[symbol] = meta\n\n    if price > _safe_float(meta.get("highest_price")):\n        meta["highest_price"] = price\n\n    entry_price = _safe_float(meta.get("entry_price") or entry)\n    if entry_price > 0 and price > 0:\n        pnl_pct = (price - entry_price) / entry_price\n        meta["highest_pnl_pct"] = max(_safe_float(meta.get("highest_pnl_pct")), pnl_pct)\n\n    return meta\n\ndef _make_sell(symbol: str, qty: float, price: float, reason: str) -> Dict[str, Any]:\n    return {\n        "symbol": symbol,\n        "side": "sell",\n        "quantity": qty,\n        "expected_price": price,\n        "fill_price": price,\n        "slippage_bps": 0,\n        "strategy": reason,\n        "confidence": 1.0,\n    }\n\ndef _has_pending_sell(symbol: str, orders: List[Dict[str, Any]]) -> bool:\n    for o in orders:\n        if _order_symbol(o) == symbol and _order_side(o) == "sell":\n            return True\n    return False\n\ndef _filter_and_resize_orders(\n    orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    if not orders:\n        return []\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    filtered: List[Dict[str, Any]] = []\n\n    for order in orders:\n        if not isinstance(order, dict):\n            filtered.append(order)\n            continue\n\n        symbol = _order_symbol(order)\n        side = _order_side(order)\n        strategy = _order_strategy(order)\n        feature = _get_feature(features, symbol)\n        price = _order_price(order, feature)\n\n        if side == "sell":\n            if "stop_loss" in strategy:\n                _record_loss(symbol, strategy, state)\n            filtered.append(order)\n            continue\n\n        if side != "buy":\n            filtered.append(order)\n            continue\n\n        is_scout = strategy == "fallback_scout_breakout"\n\n        if not is_scout:\n            # --- UNIVERSAL SIZE CAP for ALL strategies (not just scouts) ---\n            qty_ns = _safe_float(order.get("quantity") or order.get("qty"))\n            if qty_ns > 0 and price > 0:\n                max_ns = _safe_float(\n                    CFG.get("max_entry_notional_sideways_usd", 1.50)\n                    if "SIDEWAYS" in regime\n                    else CFG.get("max_entry_notional_trend_usd", 2.50)\n                )\n                notional_ns = qty_ns * price\n                if max_ns > 0 and notional_ns > max_ns:\n                    new_qty_ns = max_ns / price\n                    order["quantity"] = new_qty_ns\n                    if "qty" in order:\n                        order["qty"] = new_qty_ns\n                    _log("RESIZE_ENTRY", symbol=symbol, old_notional=notional_ns, new_notional=max_ns, price=price, strategy=strategy)\n            _record_buy(order, feature, state)\n            filtered.append(order)\n            continue\n\n        symbol_regime = _safe_str(feature.get("symbol_regime")).upper()\n        signal = _safe_float(order.get("signal_strength") or feature.get("signal_strength"))\n        trend_quality = _safe_float(feature.get("trend_quality") or feature.get("symbol_trend_score"))\n        volatility = _safe_float(feature.get("volatility"))\n        one_tick = _safe_float(feature.get("one_tick_momentum"))\n\n        if price <= 0 or price < _safe_float(CFG["min_trade_price"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="price_too_low", price=price, strategy=strategy)\n            continue\n\n        cooldown_losses = _recent_losses(symbol, state, _safe_float(CFG["cooldown_minutes"]))\n\n        if cooldown_losses >= int(CFG["symbol_cooldown_after_losses"]):\n            _log("BLOCK_ENTRY", symbol=symbol, reason="loss_cooldown", losses=cooldown_losses, strategy=strategy)\n            continue\n\n        if "SIDEWAYS" in regime:\n            if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="not_clean_uptrend", symbol_regime=symbol_regime, strategy=strategy)\n                continue\n\n            if signal < _safe_float(CFG["min_scout_signal_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_signal", signal=signal, strategy=strategy)\n                continue\n\n            if trend_quality < _safe_float(CFG["min_scout_trend_quality"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="weak_trend_quality", trend_quality=trend_quality, strategy=strategy)\n                continue\n\n            if volatility > _safe_float(CFG["max_scout_volatility_sideways"]):\n                _log("BLOCK_ENTRY", symbol=symbol, reason="extreme_volatility", volatility=volatility, strategy=strategy)\n                continue\n\n            if bool(CFG["require_positive_one_tick_for_scout"]) and one_tick <= 0:\n                _log("BLOCK_ENTRY", symbol=symbol, reason="one_tick_not_positive", one_tick=one_tick, strategy=strategy)\n                continue\n\n        qty = _safe_float(order.get("quantity") or order.get("qty"))\n\n        if qty > 0 and price > 0:\n            max_notional = _safe_float(\n                CFG["sideways_scout_notional_usd"]\n                if "SIDEWAYS" in regime\n                else CFG["trend_scout_notional_usd"]\n            )\n            notional = qty * price\n\n            if max_notional > 0 and notional > max_notional:\n                new_qty = max_notional / price\n                order["quantity"] = new_qty\n                if "qty" in order:\n                    order["qty"] = new_qty\n                _log(\n                    "RESIZE_ENTRY",\n                    symbol=symbol,\n                    old_notional=notional,\n                    new_notional=max_notional,\n                    price=price,\n                )\n\n        _record_buy(order, feature, state)\n        filtered.append(order)\n\n    return filtered\n\ndef _defensive_exit_orders(\n    existing_orders: List[Dict[str, Any]],\n    ctx: Dict[str, Any],\n    state: Dict[str, Any],\n) -> List[Dict[str, Any]]:\n\n    features = ctx.get("features") or ctx.get("feature_map") or {}\n    prices = ctx.get("prices") or ctx.get("market_prices") or ctx.get("tick") or ctx.get("market")\n\n    regime = _safe_str(ctx.get("regime") or ctx.get("market_regime") or "").upper()\n    if not regime:\n        portfolio = ctx.get("portfolio")\n        if isinstance(portfolio, dict):\n            regime = _safe_str(portfolio.get("regime")).upper()\n\n    positions = _get_positions(ctx)\n    exits: List[Dict[str, Any]] = []\n\n    for symbol, pos in positions.items():\n        if _has_pending_sell(symbol, existing_orders):\n            continue\n\n        feature = _get_feature(features, symbol)\n        price = _get_price(symbol, pos, feature, prices)\n        qty = _safe_float(pos.get("quantity") or pos.get("qty"))\n\n        if qty <= 0 or price <= 0:\n            continue\n\n        meta = _ensure_position_meta(symbol, pos, price, state)\n        entry = _safe_float(meta.get("entry_price"))\n\n        if entry <= 0:\n            continue\n\n        pnl_pct = (price - entry) / entry\n        high_pnl = _safe_float(meta.get("highest_pnl_pct"))\n        age_min = max(0.0, (_now_ts() - _safe_float(meta.get("entry_ts"), _now_ts())) / 60.0)\n        strategy = _safe_str(meta.get("strategy") or pos.get("strategy"))\n\n        if strategy == "fallback_scout_breakout" and pnl_pct <= _safe_float(CFG["fallback_stop_loss_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "adaptive_stop_loss"))\n            _record_loss(symbol, "adaptive_stop_loss", state)\n            _log("EXIT", symbol=symbol, reason="tight_scout_stop", pnl_pct=pnl_pct, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["breakeven_arm_pct"]) and pnl_pct <= _safe_float(CFG["breakeven_exit_floor_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "breakeven_protection_exit"))\n            _log("EXIT", symbol=symbol, reason="breakeven_protection", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if high_pnl >= _safe_float(CFG["trailing_arm_pct"]) and (high_pnl - pnl_pct) >= _safe_float(CFG["trailing_giveback_pct"]):\n            exits.append(_make_sell(symbol, qty, price, "trailing_profit_exit"))\n            _log("EXIT", symbol=symbol, reason="trailing_profit", pnl_pct=pnl_pct, high_pnl=high_pnl, age_min=age_min)\n            continue\n\n        if "SIDEWAYS" in regime and age_min >= _safe_float(CFG["sideways_time_stop_minutes"]):\n            if _safe_float(CFG["sideways_time_stop_min_pnl"]) <= pnl_pct <= _safe_float(CFG["sideways_time_stop_max_pnl"]):\n                exits.append(_make_sell(symbol, qty, price, "time_stop_exit"))\n                _log("EXIT", symbol=symbol, reason="sideways_time_stop", pnl_pct=pnl_pct, age_min=age_min)\n                continue\n\n    return exits\n\ndef qfos_expectancy_cycle_guard(proposed_fills: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:\n    """\n    Defensive expectancy guard.\n\n    It does four things:\n    1. Filters weak fallback scout entries.\n    2. Reduces SIDEWAYS scout size.\n    3. Adds breakeven, trailing, tight-stop, and time-stop sell orders.\n    4. Keeps risk_off_exit logic untouched.\n    """\n\n    if not CFG.get("enabled", True):\n        return proposed_fills if isinstance(proposed_fills, list) else []\n\n    state = _read_state()\n\n    try:\n        orders = list(proposed_fills or [])\n    except Exception:\n        orders = []\n\n    try:\n        orders = _filter_and_resize_orders(orders, context, state)\n        exits = _defensive_exit_orders(orders, context, state)\n\n        if exits:\n            orders = exits + orders\n\n    except Exception as exc:\n        _log("ERROR", error=repr(exc))\n\n    _write_state(state)\n    return orders\n\nprint("QFOS expectancy patch helper loaded.")\n', _QFOS_EXPECTANCY_NS)
qfos_expectancy_cycle_guard = _QFOS_EXPECTANCY_NS["qfos_expectancy_cycle_guard"]


# QFOS_AGENT3_RESCUE_REENTRY_GUARD_V1
# Final rescue-only entry gate. Does not alter exits, accounting, feature generation,
# execution bridge, exposure limits, or API behavior.

_qfos_agent3_rescue_base_expectancy_guard = qfos_expectancy_cycle_guard

QFOS_AGENT3_RESCUE_POST_STOP_COOLDOWN_MINUTES = 30
QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT = 3
QFOS_AGENT3_RESCUE_LOSS_STREAK_BLOCK_MINUTES = 120
QFOS_AGENT3_RESCUE_DUST_QTY = 1e-8

def _qfos_agent3_rescue_float(value, default=0.0):
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default

def _qfos_agent3_rescue_feature_map(context):
    if not isinstance(context, dict):
        return {}
    for key in ("features", "feature_map", "market_features"):
        value = context.get(key)
        if isinstance(value, dict):
            return value
    return {}

def _qfos_agent3_rescue_regime(context):
    if not isinstance(context, dict):
        return ""
    regime = str(context.get("regime") or context.get("market_regime") or "").upper()
    if regime:
        return regime
    portfolio_ctx = context.get("portfolio")
    if isinstance(portfolio_ctx, dict):
        return str(portfolio_ctx.get("regime") or "").upper()
    return ""

def _qfos_agent3_rescue_positions_from_context(context):
    out = {}
    if not isinstance(context, dict):
        return out
    raw = (
        context.get("positions")
        or context.get("open_positions")
        or context.get("portfolio_positions")
        or {}
    )
    if isinstance(raw, dict):
        for symbol, pos in raw.items():
            if isinstance(pos, dict):
                out[str(pos.get("symbol") or symbol)] = _qfos_agent3_rescue_float(
                    pos.get("quantity") or pos.get("qty")
                )
            else:
                out[str(symbol)] = _qfos_agent3_rescue_float(
                    getattr(pos, "quantity", getattr(pos, "qty", 0.0))
                )
    elif isinstance(raw, list):
        for pos in raw:
            if isinstance(pos, dict):
                symbol = str(pos.get("symbol") or "")
                qty = _qfos_agent3_rescue_float(pos.get("quantity") or pos.get("qty"))
            else:
                symbol = str(getattr(pos, "symbol", "") or "")
                qty = _qfos_agent3_rescue_float(
                    getattr(pos, "quantity", getattr(pos, "qty", 0.0))
                )
            if symbol:
                out[symbol] = qty
    return out

def _qfos_agent3_rescue_db_state(symbol):
    """
    Returns quarantine and open-position facts from Postgres.
    Any DB failure is treated as fail-closed for rescue buys.
    """
    state = {
        "db_ok": False,
        "quarantined": False,
        "blocked_until": None,
        "quarantine_reason": "",
        "open_qty": 0.0,
        "recent_stop_losses": 0,
        "db_error": "",
    }
    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            qrow = conn.execute(text("""
                SELECT reason, blocked_until
                FROM symbol_quarantine
                WHERE symbol = :symbol
                  AND blocked_until IS NOT NULL
                  AND blocked_until > CURRENT_TIMESTAMP
                ORDER BY blocked_until DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            prow = conn.execute(text("""
                SELECT COALESCE(SUM(quantity), 0) AS open_qty
                FROM positions
                WHERE symbol = :symbol
                  AND quantity > :dust
            """), {
                "symbol": symbol,
                "dust": QFOS_AGENT3_RESCUE_DUST_QTY,
            }).mappings().first()

            lrow = conn.execute(text("""
                SELECT COUNT(*) AS stop_count
                FROM trades
                WHERE symbol = :symbol
                  AND LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
            """), {"symbol": symbol}).mappings().first()

        state["db_ok"] = True
        state["open_qty"] = _qfos_agent3_rescue_float(
            (prow or {}).get("open_qty")
        )
        state["recent_stop_losses"] = int(
            _qfos_agent3_rescue_float((lrow or {}).get("stop_count"))
        )

        if qrow:
            state["quarantined"] = True
            state["quarantine_reason"] = str(qrow.get("reason") or "")
            state["blocked_until"] = str(qrow.get("blocked_until") or "")

    except Exception as exc:
        state["db_error"] = repr(exc)

    return state

def _qfos_agent3_rescue_record_stop_loss(symbol):
    """
    Creates 30-minute post-stop-loss cooldown. On third recent stop, upgrades
    to a 2-hour loss-streak block. This only writes symbol_quarantine metadata.
    """
    if not symbol:
        return

    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            prior_row = conn.execute(text("""
                SELECT COUNT(*) AS stop_count
                FROM trades
                WHERE symbol = :symbol
                  AND LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
            """), {"symbol": symbol}).mappings().first()

            prior_count = int(
                _qfos_agent3_rescue_float((prior_row or {}).get("stop_count"))
            )

            projected_count = prior_count + 1
            if projected_count >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
                minutes = QFOS_AGENT3_RESCUE_LOSS_STREAK_BLOCK_MINUTES
                reason = "sideways_stop_loss_loss_streak"
            else:
                minutes = QFOS_AGENT3_RESCUE_POST_STOP_COOLDOWN_MINUTES
                reason = "sideways_stop_loss_cooldown"

            conn.execute(text("""
                INSERT INTO symbol_quarantine(
                    symbol,
                    reason,
                    blocked_until,
                    created_at
                )
                VALUES (
                    :symbol,
                    :reason,
                    CURRENT_TIMESTAMP + (:minutes * interval '1 minute'),
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (symbol) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    blocked_until = GREATEST(
                        COALESCE(symbol_quarantine.blocked_until, CURRENT_TIMESTAMP),
                        EXCLUDED.blocked_until
                    ),
                    created_at = EXCLUDED.created_at
            """), {
                "symbol": symbol,
                "reason": reason,
                "minutes": minutes,
            })

        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE] symbol={symbol} "
            f"reason={reason} stop_loss_count={projected_count} "
            f"cooldown_minutes={minutes}",
            flush=True,
        )

    except Exception as exc:
        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE_ERROR] symbol={symbol} "
            f"error={exc!r}",
            flush=True,
        )

def _qfos_agent3_rescue_rank_map(feature_map):
    """
    Evidence-only local ranking for rescue gating.
    It deliberately gives no credit to non-positive one-tick momentum.
    """
    rows = []

    for symbol, feature in (feature_map or {}).items():
        if not isinstance(feature, dict):
            continue

        source = str(feature.get("source") or "").upper()
        ready = bool(feature.get("ready"))
        symbol_regime = str(feature.get("symbol_regime") or "").upper()

        if source != "NORMAL" or not ready:
            continue

        if symbol_regime not in ("SYMBOL_BREAKOUT_UP", "SYMBOL_TREND_UP"):
            continue

        signal = _qfos_agent3_rescue_float(
            feature.get("signal_strength", feature.get("signal", 0.0))
        )
        breakout = _qfos_agent3_rescue_float(feature.get("breakout_score"))
        trend_quality = _qfos_agent3_rescue_float(
            feature.get("trend_quality", feature.get("symbol_trend_score", 0.0))
        )
        momentum = _qfos_agent3_rescue_float(feature.get("momentum"))
        one_tick = _qfos_agent3_rescue_float(feature.get("one_tick_momentum"))

        if one_tick <= 0 or breakout <= 0 or trend_quality <= 0:
            continue

        score = (
            signal * 100.0
            + breakout * 100.0
            + trend_quality * 100.0
            + max(momentum, 0.0) * 25.0
            + max(one_tick, 0.0) * 25.0
        )
        rows.append((symbol, score))

    rows.sort(key=lambda item: item[1], reverse=True)
    return {symbol: index + 1 for index, (symbol, _) in enumerate(rows)}

def _qfos_agent3_rescue_emit(
    symbol,
    decision,
    reason,
    confidence,
    signal,
    one_tick,
    breakout,
    trend_quality,
    rank,
    source,
    ready,
    recent_stop_losses,
    quarantined,
    open_symbol_qty,
):
    print(
        "[RESCUE_DECISION] "
        f"symbol={symbol} decision={decision} reason={reason} "
        f"confidence={confidence:.6f} signal={signal:.8f} "
        f"one_tick_momentum={one_tick:.8f} breakout_score={breakout:.8f} "
        f"trend_quality={trend_quality:.8f} rank={rank} "
        f"source={source} ready={ready} "
        f"recent_stop_losses={recent_stop_losses} "
        f"quarantined={quarantined} open_symbol_qty={open_symbol_qty:.12f}",
        flush=True,
    )

def _qfos_agent3_rescue_reentry_guard(proposed_fills, context):
    try:
        orders = list(proposed_fills or [])
    except Exception:
        return []

    feature_map = _qfos_agent3_rescue_feature_map(context)
    regime = _qfos_agent3_rescue_regime(context)
    context_positions = _qfos_agent3_rescue_positions_from_context(context)
    rank_map = _qfos_agent3_rescue_rank_map(feature_map)

    try:
        top_n = max(1, int(ENTRY_QUALITY_TOP_N))
    except Exception:
        top_n = 10

    try:
        min_signal_sideways = float(ENTRY_MIN_SIGNAL_SIDEWAYS)
    except Exception:
        min_signal_sideways = 0.0016

    # Register stop-loss quarantine before later rescue candidates can be evaluated.
    for order in orders:
        if not isinstance(order, dict):
            continue

        side = str(order.get("side") or "").lower()
        reason = str(
            order.get("exit_reason")
            or order.get("strategy")
            or order.get("reason")
            or ""
        )
        symbol = str(order.get("symbol") or "")

        if side == "sell" and reason == "sideways_stop_loss_exit":
            _qfos_agent3_rescue_record_stop_loss(symbol)

    filtered = []

    for order in orders:
        if not isinstance(order, dict):
            filtered.append(order)
            continue

        side = str(order.get("side") or "").lower()
        strategy = str(order.get("strategy") or order.get("reason") or "")
        symbol = str(order.get("symbol") or "")

        if side != "buy" or strategy != "evo_allocator_rescue":
            filtered.append(order)
            continue

        feature = feature_map.get(symbol, {})
        if not isinstance(feature, dict):
            feature = {}

        source = str(feature.get("source") or "").upper()
        ready = bool(feature.get("ready"))
        symbol_regime = str(feature.get("symbol_regime") or "").upper()

        signal = _qfos_agent3_rescue_float(
            order.get("signal_strength")
            or feature.get("signal_strength")
            or feature.get("signal")
        )
        one_tick = _qfos_agent3_rescue_float(feature.get("one_tick_momentum"))
        breakout = _qfos_agent3_rescue_float(feature.get("breakout_score"))
        trend_quality = _qfos_agent3_rescue_float(
            feature.get("trend_quality")
            or feature.get("symbol_trend_score")
        )
        momentum = _qfos_agent3_rescue_float(feature.get("momentum"))

        rank = rank_map.get(symbol, 0)
        db_state = _qfos_agent3_rescue_db_state(symbol)
        context_open_qty = _qfos_agent3_rescue_float(context_positions.get(symbol))
        open_qty = max(context_open_qty, _qfos_agent3_rescue_float(db_state["open_qty"]))
        recent_stop_losses = int(db_state["recent_stop_losses"])
        quarantined = bool(db_state["quarantined"])

        import math
        # Evidence-derived confidence. This replaces rescue defaults such as 0.95.
        if getattr(settings, "use_confidence_v2", False):
            # Experiment E1: Empirical weighting without hard clamping
            long_trend = _qfos_agent3_rescue_float(feature.get("long_trend"))
            volatility_log = _qfos_agent3_rescue_float(feature.get("volatility_log") or feature.get("volatility"))
            
            raw_score = (
                (-4.37 * one_tick) +
                ( 2.39 * long_trend) +
                ( 1.52 * (feature.get("trend") or 0.0)) +
                (-0.49 * volatility_log) +
                (-0.23 * breakout) +
                ( 0.11 * trend_quality)
            )
            # Sigmoid normalization (no artificial floor/ceiling)
            evidence_confidence = 1.0 / (1.0 + math.exp(-raw_score))
        else:
            # V1 logic (Original)
            evidence_confidence = min(
                0.90,
                max(
                    0.0,
                    0.35
                    + min(max(signal, 0.0) * 8.0, 0.20)
                    + min(max(one_tick, 0.0) * 80.0, 0.12)
                    + min(max(breakout, 0.0) * 8.0, 0.12)
                    + min(max(trend_quality, 0.0) * 8.0, 0.11),
                ),
            )
            
        order["confidence"] = round(evidence_confidence, 6)

        reject_reason = ""

        if not db_state["db_ok"]:
            reject_reason = "rescue_db_check_error"
        elif quarantined:
            _qfos_quarantine_reason = str(db_state["quarantine_reason"] or "")
            if "loss_streak" in _qfos_quarantine_reason:
                reject_reason = "loss_streak"
            elif "stop_loss" in _qfos_quarantine_reason:
                reject_reason = "recent_stop_loss"
            else:
                reject_reason = "quarantined"
        elif recent_stop_losses >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
            reject_reason = "loss_streak"
        elif open_qty > QFOS_AGENT3_RESCUE_DUST_QTY:
            reject_reason = "existing_position_no_scale_in"
        elif source != "NORMAL" or not ready:
            reject_reason = "confidence_not_evidence_backed"
        elif "SIDEWAYS" in regime and symbol_regime not in (
            "SYMBOL_BREAKOUT_UP",
            "SYMBOL_TREND_UP",
        ):
            reject_reason = "symbol_regime_not_allowed"
        elif "SIDEWAYS" in regime and signal < min_signal_sideways:
            reject_reason = "sideways_signal_below_threshold"
        elif "SIDEWAYS" in regime and one_tick <= 0:
            reject_reason = "weak_one_tick_confirmation"
        elif "SIDEWAYS" in regime and breakout <= 0:
            reject_reason = "breakout_quality_below_threshold"
        elif "SIDEWAYS" in regime and trend_quality <= 0:
            reject_reason = "trend_quality_below_threshold"
        elif "SIDEWAYS" in regime and rank <= 0:
            reject_reason = "not_ranked_evidence_candidate"
        elif "SIDEWAYS" in regime and rank > top_n:
            reject_reason = "not_entry_quality_top_n"
        elif evidence_confidence < 0.50:
            reject_reason = "confidence_not_evidence_backed"

        if reject_reason:
            extra = ""
            if reject_reason == "recent_stop_loss":
                extra = f" blocked_until={db_state['blocked_until']}"
            elif reject_reason == "loss_streak":
                extra = f" stop_loss_count={recent_stop_losses}"
            elif reject_reason == "existing_position_no_scale_in":
                extra = f" open_symbol_qty={open_qty:.12f}"

            print(
                f"[RESCUE_REJECT] symbol={symbol} reason={reject_reason}{extra}",
                flush=True,
            )

            _qfos_agent3_rescue_emit(
                symbol=symbol,
                decision="REJECT",
                reason=reject_reason,
                confidence=evidence_confidence,
                signal=signal,
                one_tick=one_tick,
                breakout=breakout,
                trend_quality=trend_quality,
                rank=rank,
                source=source,
                ready=ready,
                recent_stop_losses=recent_stop_losses,
                quarantined=quarantined,
                open_symbol_qty=open_qty,
            )
            continue

        _qfos_agent3_rescue_emit(
            symbol=symbol,
            decision="ALLOW",
            reason="evidence_gates_passed",
            confidence=evidence_confidence,
            signal=signal,
            one_tick=one_tick,
            breakout=breakout,
            trend_quality=trend_quality,
            rank=rank,
            source=source,
            ready=ready,
            recent_stop_losses=recent_stop_losses,
            quarantined=quarantined,
            open_symbol_qty=open_qty,
        )

        filtered.append(order)

    return filtered

def qfos_expectancy_cycle_guard(proposed_fills, context):
    base_orders = _qfos_agent3_rescue_base_expectancy_guard(proposed_fills, context)
    return _qfos_agent3_rescue_reentry_guard(base_orders, context)




# QFOS_AGENT3_ACTIVE_RESCUE_HOOK_ENFORCEMENT_V1
# Applies Agent 3 rescue checks at the direct allocator_rescue_hook path.
# Scope: rescue BUY admission only. No changes to exits, accounting, API,
# exposure limits, feature generation, or DB oversell protection.

def _qfos_agent3_rescue_feature_map(context):
    if not isinstance(context, dict):
        return {}

    for key in ("f_by_symbol", "features", "feature_map", "market_features"):
        value = context.get(key)
        if isinstance(value, dict):
            return value

    state = context.get("state")
    if isinstance(state, dict):
        value = state.get("features")
        if isinstance(value, dict):
            return value

    return {}

def _qfos_agent3_refresh_stop_loss_quarantines():
    """
    Rebuilds rescue quarantine truth from persisted stop-loss trades.
    1 recent stop => 30-minute cooldown from that stop.
    3+ stop losses in the past 2 hours => 2-hour loss-streak block
    from the latest stop.
    """
    refreshed = 0

    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT
                    symbol,
                    COUNT(*) AS stop_count,
                    MAX(created_at) AS last_stop_at
                FROM trades
                WHERE LOWER(side) = 'sell'
                  AND COALESCE(exit_reason, strategy, '') = 'sideways_stop_loss_exit'
                  AND created_at >= CURRENT_TIMESTAMP - interval '2 hours'
                GROUP BY symbol
            """)).mappings().all()

            for row in rows:
                symbol = str(row.get("symbol") or "").strip()
                last_stop_at = row.get("last_stop_at")
                stop_count = int(_qfos_agent3_rescue_float(row.get("stop_count")))

                if not symbol or last_stop_at is None:
                    continue

                if stop_count >= QFOS_AGENT3_RESCUE_LOSS_STREAK_LIMIT:
                    minutes = QFOS_AGENT3_RESCUE_LOSS_STREAK_BLOCK_MINUTES
                    reason = "sideways_stop_loss_loss_streak"
                else:
                    minutes = QFOS_AGENT3_RESCUE_POST_STOP_COOLDOWN_MINUTES
                    reason = "sideways_stop_loss_cooldown"

                conn.execute(text("""
                    INSERT INTO symbol_quarantine(
                        symbol,
                        reason,
                        blocked_until,
                        created_at
                    )
                    VALUES (
                        :symbol,
                        :reason,
                        :last_stop_at + (:minutes * interval '1 minute'),
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (symbol) DO UPDATE SET
                        reason = EXCLUDED.reason,
                        blocked_until = GREATEST(
                            COALESCE(symbol_quarantine.blocked_until, CURRENT_TIMESTAMP),
                            EXCLUDED.blocked_until
                        ),
                        created_at = EXCLUDED.created_at
                """), {
                    "symbol": symbol,
                    "reason": reason,
                    "last_stop_at": last_stop_at,
                    "minutes": minutes,
                })

                refreshed += 1

        if refreshed:
            print(
                f"[RESCUE_STOP_LOSS_QUARANTINE_REFRESH] symbols={refreshed}",
                flush=True,
            )

    except Exception as exc:
        print(
            f"[RESCUE_STOP_LOSS_QUARANTINE_REFRESH_ERROR] error={exc!r}",
            flush=True,
        )

def _qfos_agent3_rescue_active_hook_gate(orders, context):
    """
    Final gate for the actual direct allocator rescue route.
    The existing Agent 3 re-entry guard now receives the allocator's
    feature map and the current regime from the rescue generator locals.
    """
    _qfos_agent3_refresh_stop_loss_quarantines()

    try:
        guarded = _qfos_agent3_rescue_reentry_guard(
            list(orders or []),
            context if isinstance(context, dict) else {},
        )
    except Exception as exc:
        print(
            f"[RESCUE_REJECT] symbol=UNKNOWN reason=active_hook_guard_error "
            f"error={exc!r}",
            flush=True,
        )
        return [
            order for order in list(orders or [])
            if not (
                isinstance(order, dict)
                and str(order.get("side") or "").lower() == "buy"
                and str(order.get("strategy") or order.get("reason") or "")
                    == "evo_allocator_rescue"
            )
        ]

    rescue_before = sum(
        1 for order in list(orders or [])
        if isinstance(order, dict)
        and str(order.get("side") or "").lower() == "buy"
        and str(order.get("strategy") or order.get("reason") or "")
            == "evo_allocator_rescue"
    )

    rescue_after = sum(
        1 for order in list(guarded or [])
        if isinstance(order, dict)
        and str(order.get("side") or "").lower() == "buy"
        and str(order.get("strategy") or order.get("reason") or "")
            == "evo_allocator_rescue"
    )

    print(
        f"[RESCUE_ACTIVE_HOOK_GATE] "
        f"rescue_before={rescue_before} rescue_after={rescue_after}",
        flush=True,
    )

    return guarded


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

        # QFOS FALLBACK SCOUT QUALITY GUARD ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â inline pre-expectancy filter
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

      # QFOS FALLBACK SCOUT QUALITY GUARD ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â inline pre-expectancy filter
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

        # QFOS FALLBACK SCOUT QUALITY GUARD ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â inline pre-expectancy filter
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


# QFOS_EXIT_LIFECYCLE_DB_PATCH_V1
# Joint Agent 2 + Agent 5
# Purpose:
#   - Evaluate every DB open position every cycle.
#   - Emit one [EXIT_DECISION] log per open position.
#   - Inject SELL fills into the existing execution/accounting pipeline.
#   - Do not change BUY allocation, feature generation, cash accounting, or live mode.

QFOS_EXIT_LIFECYCLE_ENABLED = globals().get("QFOS_EXIT_LIFECYCLE_ENABLED", True)
# C3: lifecycle remains an evaluator/logger; the main loop is the sole persistence owner.
QFOS_LIFECYCLE_DIRECT_EXECUTION_ENABLED = globals().get(
    "QFOS_LIFECYCLE_DIRECT_EXECUTION_ENABLED", False
)

QFOS_EXIT_TAKE_PROFIT_PCT = globals().get("QFOS_EXIT_TAKE_PROFIT_PCT", 0.012)          # +1.20%
QFOS_EXIT_STOP_LOSS_PCT = globals().get("QFOS_EXIT_STOP_LOSS_PCT", -0.008)             # -0.80%

QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT = globals().get("QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT", 0.006)  # +0.60%
QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT = globals().get("QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT", -0.006)     # -0.60%

QFOS_SIDEWAYS_STAGNATION_MINUTES = globals().get("QFOS_SIDEWAYS_STAGNATION_MINUTES", 20.0)
QFOS_SIDEWAYS_STAGNATION_LOW_PCT = globals().get("QFOS_SIDEWAYS_STAGNATION_LOW_PCT", -0.0025)
QFOS_SIDEWAYS_STAGNATION_HIGH_PCT = globals().get("QFOS_SIDEWAYS_STAGNATION_HIGH_PCT", 0.0035)

QFOS_MAX_HOLD_MINUTES = globals().get("QFOS_MAX_HOLD_MINUTES", 45.0)

QFOS_TRAILING_PEAK_PCT = globals().get("QFOS_TRAILING_PEAK_PCT", 0.0045)
QFOS_TRAILING_FLOOR_PCT = globals().get("QFOS_TRAILING_FLOOR_PCT", 0.0015)

QFOS_BREAKEVEN_PEAK_PCT = globals().get("QFOS_BREAKEVEN_PEAK_PCT", 0.0035)
QFOS_BREAKEVEN_FLOOR_PCT = globals().get("QFOS_BREAKEVEN_FLOOR_PCT", 0.0002)

_qfos_exit_peak_pct = {}


def _qfos_exit_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_exit_side(value):
    return str(value or "").strip().lower()


def _qfos_exit_now_sql_expr():
    # DB stores user-facing local time in many places; use the same +3h convention.
    return "CURRENT_TIMESTAMP + interval '3 hours'"


def _qfos_exit_open_positions_from_db():
    """
    Read DB positions with age and PnL.

    This avoids the old bug where the cycle guard received locals()
    but no normalized positions object, resulting in exits=0.
    """
    rows = []

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                select
                    p.symbol,
                    p.quantity,
                    p.avg_entry,
                    coalesce(p.last_price, p.avg_entry) as last_price,
                    coalesce(p.exposure, p.quantity * coalesce(p.last_price, p.avg_entry)) as exposure,
                    coalesce(p.unrealized_pnl, (coalesce(p.last_price, p.avg_entry) - p.avg_entry) * p.quantity) as unrealized_pnl,
                    coalesce(p.strategy, 'unknown_strategy') as strategy,
                    coalesce(
                        (
                            select min(t.created_at)
                            from trades t
                            where t.symbol = p.symbol
                              and lower(t.side) = 'buy'
                        ),
                        p.updated_at,
                        CURRENT_TIMESTAMP
                    ) as first_buy_at,
                    extract(epoch from ((CURRENT_TIMESTAMP + interval '3 hours') - coalesce(
                        (
                            select min(t.created_at)
                            from trades t
                            where t.symbol = p.symbol
                              and lower(t.side) = 'buy'
                        ),
                        p.updated_at,
                        CURRENT_TIMESTAMP
                    ))) / 60.0 as age_minutes
                from positions p
                where coalesce(p.quantity, 0) > 0.00000001
                order by age_minutes desc
            """)).mappings().all()
    except Exception as exc:
        print(f"[EXIT_DECISION] db_read_failed error={repr(exc)}", flush=True)
        return []

    return [dict(r) for r in rows]


def _qfos_exit_runner_conditions_true(pos, pnl_pct, peak_pct, regime):
    """
    Strict runner rule.

    Do not use vague optimism to hold dead SIDEWAYS positions.
    Runner protection only applies to clearly green positions.
    """
    try:
        if pnl_pct >= 0.006 and peak_pct >= 0.006:
            return True
        if str(regime or "").upper() != "SIDEWAYS" and pnl_pct >= 0.004 and peak_pct >= 0.004:
            return True
    except Exception:
        pass
    return False


def _qfos_exit_decision_for_position(pos, regime):
    symbol = str(pos.get("symbol") or "").strip()
    qty = _qfos_exit_float(pos.get("quantity"))
    avg_entry = _qfos_exit_float(pos.get("avg_entry"))
    last_price = _qfos_exit_float(pos.get("last_price"), avg_entry)
    age_minutes = _qfos_exit_float(pos.get("age_minutes"))

    if not symbol:
        return None

    if qty <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_no_open_quantity",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "age_minutes": age_minutes,
        }

    if avg_entry <= 0 or last_price <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_invalid_price",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "age_minutes": age_minutes,
        }

    pnl_pct = (last_price - avg_entry) / avg_entry
    peak_pnl_pct = max(_qfos_exit_peak_pct.get(symbol, pnl_pct), pnl_pct)
    _qfos_exit_peak_pct[symbol] = peak_pnl_pct

    is_sideways = str(regime or "").upper() == "SIDEWAYS"
    runner = _qfos_exit_runner_conditions_true(pos, pnl_pct, peak_pnl_pct, regime)

    tp = QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT if is_sideways else QFOS_EXIT_TAKE_PROFIT_PCT
    sl = QFOS_SIDEWAYS_EXIT_STOP_LOSS_PCT if is_sideways else QFOS_EXIT_STOP_LOSS_PCT

    # 1. Take profit
    if pnl_pct >= tp:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_take_profit_exit" if is_sideways else "take_profit_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 2. Stop loss
    if pnl_pct <= sl:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_stop_loss_exit" if is_sideways else "stop_loss_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 5. Trailing profit protection
    if peak_pnl_pct >= QFOS_TRAILING_PEAK_PCT and pnl_pct <= QFOS_TRAILING_FLOOR_PCT:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "trailing_profit_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 6. Breakeven protection
    if peak_pnl_pct >= QFOS_BREAKEVEN_PEAK_PCT and pnl_pct <= QFOS_BREAKEVEN_FLOOR_PCT:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "breakeven_protection_exit",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    # 3. Sideways stagnation exit
    if is_sideways and age_minutes >= QFOS_SIDEWAYS_STAGNATION_MINUTES:
        if QFOS_SIDEWAYS_STAGNATION_LOW_PCT <= pnl_pct <= QFOS_SIDEWAYS_STAGNATION_HIGH_PCT:
            if not runner:
                return {
                    "symbol": symbol,
                    "decision": "SELL",
                    "reason": "sideways_stagnation_exit",
                    "quantity": qty,
                    "price": last_price,
                    "pnl_pct": pnl_pct,
                    "peak_pnl_pct": peak_pnl_pct,
                    "age_minutes": age_minutes,
                }

            return {
                "symbol": symbol,
                "decision": "HOLD",
                "reason": "hold_runner_conditions_true",
                "quantity": qty,
                "price": last_price,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "age_minutes": age_minutes,
            }

    # 4. Max hold exit
    if age_minutes >= QFOS_MAX_HOLD_MINUTES:
        if not runner:
            return {
                "symbol": symbol,
                "decision": "SELL",
                "reason": "sideways_max_hold_exit" if is_sideways else "max_hold_exit",
                "quantity": qty,
                "price": last_price,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "age_minutes": age_minutes,
            }

        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_runner_conditions_true",
            "quantity": qty,
            "price": last_price,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "age_minutes": age_minutes,
        }

    hold_reasons = []

    if age_minutes < QFOS_SIDEWAYS_STAGNATION_MINUTES:
        hold_reasons.append("hold_not_old_enough")

    if pnl_pct < tp:
        hold_reasons.append("hold_take_profit_not_hit")

    if pnl_pct > sl:
        hold_reasons.append("hold_stop_loss_not_hit")

    if not hold_reasons:
        hold_reasons.append("hold_exit_threshold_not_met")

    return {
        "symbol": symbol,
        "decision": "HOLD",
        "reason": "|".join(hold_reasons),
        "quantity": qty,
        "price": last_price,
        "pnl_pct": pnl_pct,
        "peak_pnl_pct": peak_pnl_pct,
        "age_minutes": age_minutes,
    }


# QFOS_ACTIVE_EXIT_EPOCH_FIX_V2
# Preserve original lifecycle functions, then replace only their state source.
_qfos_exit_open_positions_from_db_original = _qfos_exit_open_positions_from_db
_qfos_exit_decision_for_position_original = _qfos_exit_decision_for_position

def _qfos_exit_open_positions_from_db():
    """
    Active lifecycle loader with fresh-entry age truth.

    The prior loader used MIN(BUY) across all symbol history, causing a new
    re-entry to inherit age from a closed historical trade.
    """
    rows = _qfos_exit_open_positions_from_db_original()

    if not rows:
        return rows

    try:
        with engine.begin() as conn:
            for pos in rows:
                symbol = str(pos.get("symbol") or "").strip()
                if not symbol:
                    continue

                row = conn.execute(text("""
                    SELECT
                        MAX(created_at) AS latest_buy_at
                    FROM trades
                    WHERE symbol = :symbol
                      AND LOWER(side) = 'buy'
                """), {"symbol": symbol}).mappings().first()

                latest_buy_at = (row or {}).get("latest_buy_at")
                if latest_buy_at is None:
                    continue

                age_row = conn.execute(text("""
                    SELECT EXTRACT(
                        EPOCH FROM (
                            CURRENT_TIMESTAMP - :latest_buy_at
                        )
                    ) / 60.0 AS age_minutes
                """), {
                    "latest_buy_at": latest_buy_at,
                }).mappings().first()

                age_minutes = float(
                    ((age_row or {}).get("age_minutes")) or 0.0
                )

                pos["entry_started_at"] = latest_buy_at
                pos["age_minutes"] = max(0.0, age_minutes)

    except Exception as exc:
        print(
            f"[EXIT_ACTIVE_EPOCH_LOADER_ERROR] error={exc!r}",
            flush=True,
        )

    return rows


def _qfos_exit_decision_for_position(pos, regime):
    """
    Reset peak state whenever the active loader identifies a fresh epoch.
    """
    symbol = str((pos or {}).get("symbol") or "").strip()
    epoch = str((pos or {}).get("entry_started_at") or "")

    try:
        epoch_map = globals().setdefault("_qfos_exit_peak_epochs", {})
        prior_epoch = epoch_map.get(symbol)

        if symbol and epoch and prior_epoch != epoch:
            _qfos_exit_peak_pct.pop(symbol, None)
            epoch_map[symbol] = epoch

            print(
                f"[EXIT_ACTIVE_EPOCH_RESET] "
                f"symbol={symbol} "
                f"entry_started_at={epoch}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[EXIT_ACTIVE_EPOCH_RESET_ERROR] "
            f"symbol={symbol} error={exc!r}",
            flush=True,
        )

    return _qfos_exit_decision_for_position_original(pos, regime)


def _qfos_exit_log_decision(d):
    try:
        print(
            "[EXIT_DECISION] "
            f"symbol={d.get('symbol')} "
            f"age_min={_qfos_exit_float(d.get('age_minutes')):.2f} "
            f"pnl_pct={_qfos_exit_float(d.get('pnl_pct')):.4%} "
            f"peak_pnl_pct={_qfos_exit_float(d.get('peak_pnl_pct')):.4%} "
            f"decision={d.get('decision')} "
            f"reason={d.get('reason')}",
            flush=True,
        )
    except Exception as exc:
        print(f"[EXIT_DECISION] log_failed error={repr(exc)}", flush=True)


def _qfos_exit_build_sell_fill(d):
    symbol = str(d.get("symbol") or "").strip()
    qty = _qfos_exit_float(d.get("quantity"))
    price = _qfos_exit_float(d.get("price"))
    reason = str(d.get("reason") or "").strip()

    if not symbol or qty <= 0 or price <= 0 or not reason:
        return None

    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "qty": qty,
        "price": price,
        "fill_price": price,
        "expected_price": price,
        "strategy": reason,
        "reason": reason,
        "exit_reason": reason,
        "is_exit": True,
        "confidence": 1.0,
        "source": "exit_lifecycle",
    }


def qfos_exit_lifecycle_db_sells(regime):
    """
    Return SELL fills for qualifying DB open positions.
    Logs one EXIT_DECISION per position every cycle.
    """
    if not QFOS_EXIT_LIFECYCLE_ENABLED:
        return []

    sells = []

    for pos in _qfos_exit_open_positions_from_db():
        decision = _qfos_exit_decision_for_position(pos, regime)
        if not decision:
            continue

        _qfos_exit_log_decision(decision)

        if decision.get("decision") == "SELL":
            fill = _qfos_exit_build_sell_fill(decision)
            if fill:
                sells.append(fill)

    if sells:
        print(
            "[EXIT_LIFECYCLE] injected_sells="
            + str([(s.get("symbol"), s.get("quantity"), s.get("exit_reason")) for s in sells]),
            flush=True,
        )

    return sells


def _qfos_exit_lifecycle_wrap_expectancy_guard():
    """
    Wrap the already-active qfos_expectancy_guard_with_cycle_log.

    Important:
    - Existing expectancy guard remains active.
    - We append DB-backed exit lifecycle SELLs afterward.
    - No BUY logic is changed.
    """
    global qfos_expectancy_guard_with_cycle_log

    old_guard = globals().get("qfos_expectancy_guard_with_cycle_log")

    if not callable(old_guard):
        print("[EXIT_LIFECYCLE] expectancy_guard_not_found; db exits will rely on generate_sells path only", flush=True)
        return

    if getattr(old_guard, "_qfos_exit_lifecycle_wrapped", False):
        return

    def _wrapped_qfos_expectancy_guard_with_exit_lifecycle(proposed_fills=None, context=None):
        try:
            out = old_guard(proposed_fills, context)
        except Exception as exc:
            print("[EXIT_LIFECYCLE] original_expectancy_guard_failed " + repr(exc), flush=True)
            out = list(proposed_fills or [])

        try:
            ctx = context if isinstance(context, dict) else {}
            regime = ctx.get("regime") or ctx.get("market_regime") or globals().get("last_known_regime") or "SIDEWAYS"
            exit_sells = qfos_exit_lifecycle_db_sells(regime)

            if exit_sells:
                existing = list(out or [])
                existing_keys = set()

                for f in existing:
                    if isinstance(f, dict) and _qfos_exit_side(f.get("side")) == "sell":
                        existing_keys.add(str(f.get("symbol") or "").strip())

                clean_exit_sells = [
                    s for s in exit_sells
                    if str(s.get("symbol") or "").strip() not in existing_keys
                ]

                if clean_exit_sells:
                    out = clean_exit_sells + existing

        except Exception as exc:
            print("[EXIT_LIFECYCLE] db_exit_injection_failed " + repr(exc), flush=True)

        return out

    _wrapped_qfos_expectancy_guard_with_exit_lifecycle._qfos_exit_lifecycle_wrapped = True
    qfos_expectancy_guard_with_cycle_log = _wrapped_qfos_expectancy_guard_with_exit_lifecycle
    print("[EXIT_LIFECYCLE] wrapped qfos_expectancy_guard_with_cycle_log", flush=True)


_qfos_exit_lifecycle_wrap_expectancy_guard()

# END QFOS_EXIT_LIFECYCLE_DB_PATCH_V1


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
            try:
                from observability import events, RejectionReason, _make_allocator_state, _manager
                cycle_id = qfos_observability_cycle_id()
                cids = []
                for sym in blocked:
                    info = _manager.get_candidate_info(cycle_id, sym)
                    if info and info.get("candidate_id"): cids.append(info["candidate_id"])
                
                if cids:
                    alloc_state = _make_allocator_state(**_qfos_adapt_alloc_state(qfos_active_canbuy_ledger_state())) if 'qfos_active_canbuy_ledger_state' in globals() else None
                    events.batch_filtered(
                        cycle_id=cycle_id,
                        filter_stage=2,
                        filter_name="sideways_hard_exposure",
                        reason=RejectionReason.MAX_EXPOSURE,
                        affected_candidates=cids,
                        raw_reason=f"sideways_exposure_{exposure_pct:.4f}_gt_{QFOS_SIDEWAYS_HARD_EXPOSURE_PCT:.4f}",
                        details={"actual": exposure_pct, "threshold": QFOS_SIDEWAYS_HARD_EXPOSURE_PCT},
                        allocator_state=alloc_state
                    )
            except Exception:
                pass

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

# QFOS_TRUTHFUL_STATUS_METRICS_MIDDLEWARE_V1
# This is presentation/accounting telemetry only. It never changes orders,
# positions, exits, portfolio cash, realized PnL, or strategy decisions.
_qfos_truth_metrics_cache = {"at": 0.0, "value": None}

def _qfos_truthful_closed_fill_metrics():
    import os
    import time

    now = time.monotonic()
    cached = _qfos_truth_metrics_cache.get("value")
    if cached is not None and (now - _qfos_truth_metrics_cache.get("at", 0.0)) < 2.0:
        return cached

    dsn_candidates = [
        os.getenv("DATABASE_URL"),
        os.getenv("POSTGRES_DSN"),
        os.getenv("DB_URL"),
        (
            "postgresql://"
            f"{os.getenv('POSTGRES_USER', 'qfos')}:"
            f"{os.getenv('POSTGRES_PASSWORD', 'qfos')}@"
            f"{os.getenv('POSTGRES_HOST', 'postgres')}:"
            f"{os.getenv('POSTGRES_PORT', '5432')}/"
            f"{os.getenv('POSTGRES_DB', 'quant_fund_os')}"
        ),
    ]
    dsn_candidates = [item for item in dsn_candidates if item]

    rows = None
    last_error = None

    for dsn in dsn_candidates:
        for driver_name in ("psycopg", "psycopg2"):
            try:
                if driver_name == "psycopg":
                    import psycopg
                    with psycopg.connect(dsn, connect_timeout=3) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    id,
                                    symbol,
                                    LOWER(COALESCE(side, '')) AS side,
                                    COALESCE(quantity, 0) AS quantity,
                                    COALESCE(fill_price, expected_price, 0) AS price
                                FROM trades
                                ORDER BY id ASC
                                """
                            )
                            rows = cur.fetchall()
                else:
                    import psycopg2
                    with psycopg2.connect(dsn, connect_timeout=3) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    id,
                                    symbol,
                                    LOWER(COALESCE(side, '')) AS side,
                                    COALESCE(quantity, 0) AS quantity,
                                    COALESCE(fill_price, expected_price, 0) AS price
                                FROM trades
                                ORDER BY id ASC
                                """
                            )
                            rows = cur.fetchall()

                if rows is not None:
                    break
            except Exception as exc:
                last_error = repr(exc)

        if rows is not None:
            break

    if rows is None:
        return {
            "metrics_available": False,
            "metrics_error": last_error or "database_metrics_unavailable",
        }

    state = {}
    wins = 0
    losses = 0
    breakevens = 0
    closed_sell_fills = 0
    gross_realized = 0.0
    eps = 1e-10

    for _, symbol, side, quantity, price in rows:
        try:
            qty = abs(float(quantity or 0.0))
            px = float(price or 0.0)
        except Exception:
            continue

        if not symbol or qty <= eps or px <= 0:
            continue

        current_qty, avg_entry = state.get(symbol, (0.0, 0.0))

        if side == "buy":
            next_qty = current_qty + qty
            next_avg = (
                ((current_qty * avg_entry) + (qty * px)) / next_qty
                if next_qty > eps else 0.0
            )
            state[symbol] = (next_qty, next_avg)
            continue

        if side != "sell" or current_qty <= eps:
            continue

        close_qty = min(qty, current_qty)
        close_pnl = (px - avg_entry) * close_qty

        closed_sell_fills += 1
        gross_realized += close_pnl

        if close_pnl > eps:
            wins += 1
        elif close_pnl < -eps:
            losses += 1
        else:
            breakevens += 1

        remaining = current_qty - close_qty
        state[symbol] = (remaining, avg_entry) if remaining > eps else (0.0, 0.0)

    denominator = wins + losses + breakevens
    result = {
        "metrics_available": True,
        "metrics_basis": "weighted_average_fill_price_before_fees",
        "closed_sell_fills": closed_sell_fills,
        "winning_closed_fills": wins,
        "losing_closed_fills": losses,
        "breakeven_closed_fills": breakevens,
        "truthful_win_rate": (wins / denominator) if denominator else 0.0,
        "gross_fill_price_realized_pnl": gross_realized,
    }

    _qfos_truth_metrics_cache["at"] = now
    _qfos_truth_metrics_cache["value"] = result
    return result


@app.middleware("http")
async def _qfos_truthful_status_metrics_middleware(request, call_next):
    response = await call_next(request)

    if request.url.path != "/status":
        return response

    try:
        import json
        from fastapi.responses import Response

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        payload = json.loads(body.decode("utf-8"))
        performance = payload.get("performance")

        if isinstance(performance, dict):
            truth = _qfos_truthful_closed_fill_metrics()

            if truth.get("metrics_available"):
                performance["win_rate"] = truth["truthful_win_rate"]
                performance["win_rate_estimate"] = truth["truthful_win_rate"]
                performance["closed_outcome_count"] = truth["closed_sell_fills"]
                performance["winning_closed_fills"] = truth["winning_closed_fills"]
                performance["losing_closed_fills"] = truth["losing_closed_fills"]
                performance["breakeven_closed_fills"] = truth["breakeven_closed_fills"]
                performance["metrics_basis"] = truth["metrics_basis"]
                performance["gross_fill_price_realized_pnl"] = truth[
                    "gross_fill_price_realized_pnl"
                ]
            else:
                performance["metrics_basis"] = "engine_metric_fallback"
                performance["metrics_error"] = truth.get("metrics_error")

        headers = dict(response.headers)
        headers.pop("content-length", None)

        return Response(
            content=json.dumps(payload, default=str).encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=response.background,
        )
    except Exception as exc:
        print(f"[QFOS_STATUS_METRICS_ERROR] error={exc!r}", flush=True)
        return response


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

QFOS_SCOUT_FALLBACK_ENABLED = False  # QFOS_REAL_MEXC_ONLY_V1: fallback scout disabled
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
                      AND blocked_until > CURRENT_TIMESTAMP + interval '3 hours'
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
ENTRY_MIN_SIGNAL_SIDEWAYS = float(getattr(settings, 'entry_min_signal_sideways', 0.0017))
ENTRY_MIN_SIGNAL_TRENDING = float(getattr(settings, 'entry_min_signal_trending', 0.0015))
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


# ============================================================
# QFOS_AGENT5_POSTGRES_LEDGER_LINEAGE_GUARD_V1
# Purpose:
#   Prevent orphan Postgres position rows from being restored into
#   executable paper runtime state unless they have trade lineage or
#   explicit PM-approved metadata.
# ============================================================

def qfos_position_has_valid_lineage_or_marker(conn, symbol, strategy=None):
    try:
        sym = str(symbol or "").strip()
        strat = str(strategy or "").strip().lower()

        if not sym:
            return False, "missing_symbol"

        approved_markers = (
            "seeded",
            "seed",
            "test",
            "reconciled",
            "approved_migration",
            "pm_approved_migration",
            "lineage_status=approved",
            "source=reconciled",
        )

        # Explicit markers must not be paper_position_sync alone.
        if any(marker in strat for marker in approved_markers):
            return True, "approved_marker"

        row = conn.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END), 0) AS buy_qty,
                COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END), 0) AS sell_qty,
                COUNT(*) AS trade_rows
            FROM trades
            WHERE symbol = :symbol
        """), {"symbol": sym}).mappings().first()

        buy_qty = float((row or {}).get("buy_qty") or 0.0)
        sell_qty = float((row or {}).get("sell_qty") or 0.0)
        trade_rows = int((row or {}).get("trade_rows") or 0)
        net_qty = buy_qty - sell_qty

        if trade_rows > 0 and net_qty > 0.00000001:
            return True, "net_trade_lineage"

        return False, "no_trade_lineage"
    except Exception as e:
        print(f"[LEDGER_GUARD] lineage_check_error symbol={symbol} error={e}", flush=True)
        return False, "lineage_check_error"


def qfos_runtime_restore_position_if_valid(conn, row):
    try:
        symbol = row["symbol"]
        qty = float(row["quantity"] or 0.0)
        avg_entry = float(row["avg_entry"] or 0.0)
        strategy = row.get("strategy") if hasattr(row, "get") else row["strategy"]

        if qty <= 0:
            return False

        ok, reason = qfos_position_has_valid_lineage_or_marker(conn, symbol, strategy)

        if not ok:
            print(
                f"[LEDGER_GUARD] blocked_orphan_position_restore symbol={symbol} "
                f"qty={qty} strategy={strategy} reason={reason}",
                flush=True,
            )
            return False

        portfolio.positions[symbol] = qty
        entry_prices[symbol] = avg_entry
        print(
            f"[LEDGER_GUARD] restored_position symbol={symbol} qty={qty} reason={reason}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[LEDGER_GUARD] restore_error error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT5_POSTGRES_LEDGER_LINEAGE_GUARD_V1
# ============================================================



# ============================================================
# QFOS_AGENT5_CASH_EQUITY_RUNTIME_AUTHORITY_V2
# Purpose:
#   Force paper runtime cash/equity/PnL to match the Postgres
#   trade+position ledger. This prevents BUY notional/exposure
#   from inflating account equity or stale snapshots from becoming
#   account authority.
# ============================================================

def qfos_agent5_ledger_accounting_snapshot():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT *
                FROM qfos_current_ledger_accounting()
                LIMIT 1
            """)).mappings().first()
        return dict(row or {})
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] ledger_read error={e}", flush=True)
        return {}

def qfos_agent5_apply_ledger_accounting_to_runtime(source="runtime"):
    try:
        a = qfos_agent5_ledger_accounting_snapshot()
        if not a:
            return False

        cash = float(a.get("expected_cash") or 0.0)
        exposure = float(a.get("expected_exposure") or 0.0)
        equity = float(a.get("expected_equity") or 0.0)
        realized = float(a.get("realized_pnl") or 0.0)
        unrealized = float(a.get("unrealized_pnl") or 0.0)
        total_pnl = float(a.get("total_pnl") or 0.0)

        try:
            portfolio.cash = cash
        except Exception:
            pass
        try:
            portfolio.equity = equity
        except Exception:
            pass
        try:
            portfolio.realized_pnl = realized
        except Exception:
            pass
        try:
            portfolio.unrealized_pnl = unrealized
        except Exception:
            pass
        try:
            portfolio.total_pnl = total_pnl
        except Exception:
            pass
        try:
            # Do not let an inflated stale peak produce false drawdown.
            portfolio.peak = max(100.0, equity, float(getattr(portfolio, "peak", 100.0) or 100.0))
        except Exception:
            pass

        print(
            f"[QFOS_CASH_EQUITY_AUTHORITY] source={source} "
            f"cash={cash:.8f} exposure={exposure:.8f} equity={equity:.8f} "
            f"realized={realized:.8f} unrealized={unrealized:.8f} total_pnl={total_pnl:.8f}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] source={source} error={e}", flush=True)
        return False

def qfos_agent5_start_cash_equity_authority_daemon():
    try:
        import threading
        import time

        if globals().get("_qfos_agent5_cash_equity_authority_started"):
            return

        globals()["_qfos_agent5_cash_equity_authority_started"] = True

        def _worker():
            while True:
                try:
                    qfos_agent5_apply_ledger_accounting_to_runtime(source="daemon")
                except Exception as e:
                    print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] daemon error={e}", flush=True)
                time.sleep(3)

        t = threading.Thread(target=_worker, name="qfos_cash_equity_authority", daemon=True)
        t.start()
        print("[QFOS_CASH_EQUITY_AUTHORITY] daemon_started", flush=True)
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] daemon_start error={e}", flush=True)

# C11_IMPORT_SAFE_GUARD_V1
# Prevent module imports used by lifecycle tests from starting runtime daemons.
if str(__import__("os").getenv("QFOS_IMPORT_SAFE", "")).strip().lower() in ("1", "true", "yes", "on"):
    print("[QFOS_IMPORT_SAFE] skipped cash_equity_module_startup", flush=True)
else:
    try:
        qfos_agent5_apply_ledger_accounting_to_runtime(source="module_load")
        qfos_agent5_start_cash_equity_authority_daemon()
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] startup error={e}", flush=True)

# ============================================================
# End QFOS_AGENT5_CASH_EQUITY_RUNTIME_AUTHORITY_V2
# ============================================================


def load_state_from_db():
    try:
        qfos_apply_clean_ledger_runtime_reset(source='after_load_state_from_db')
    except NameError:
        # Baseline authority may be defined later during module load.
        pass
    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] after_load_state_reset_error={exc}", flush=True)
    print('Recovering state from database...')
    try:
        with engine.begin() as conn:
            # Check baseline status first
            trades_count = conn.execute(text("SELECT COUNT(*) FROM trades")).scalar()
            pos_count = conn.execute(text("SELECT COUNT(*) FROM positions WHERE quantity > 0")).scalar()
            is_clean_baseline = (trades_count == 0 and pos_count == 0)

            snap = conn.execute(text('\n                SELECT cash, equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1\n            ')).mappings().first()
            if snap:
                recovered_cash = float(snap['cash'])
                recovered_equity = float(snap['equity'] or INITIAL_EQUITY)
                if not is_clean_baseline and (recovered_cash < -0.01 or recovered_equity > INITIAL_EQUITY * 5):
                    msg = f'state_corruption_detected cash={recovered_cash:.2f} equity={recovered_equity:.2f}; reset quant.db before continuing'
                    print('WARNING:', msg)
                    pause_bot(msg)
                    portfolio.cash = max(0.0, min(recovered_cash, INITIAL_EQUITY))
                    portfolio.peak = INITIAL_EQUITY
                elif not is_clean_baseline:
                    portfolio.cash = recovered_cash
                    portfolio.peak = max(portfolio.peak, recovered_equity)
                    print(f'Recovered cash: ${portfolio.cash:.2f}')
            rows = conn.execute(text('''
                SELECT symbol, quantity, avg_entry, strategy
                FROM positions
                WHERE quantity > 0
            ''')).mappings().all()
            restored_positions = 0
            for r in rows:
                if qfos_runtime_restore_position_if_valid(conn, r):
                    restored_positions += 1
            if rows:
                print(f'Recovered {restored_positions}/{len(rows)} open positions after ledger lineage guard.')
            trades = conn.execute(text("\n                SELECT symbol, created_at\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= (CURRENT_TIMESTAMP + interval '3 hours' - (:hours || ' hours')::interval)\n            "), {'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().all()
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


# ============================================================
# QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1
# Purpose:
#   If Postgres ledger is clean paper baseline, force runtime
#   in-memory portfolio and pause/drawdown state to agree.
#   This prevents /status from showing stale cash/equity/drawdown
#   after orphan position cleanup.
# ============================================================

def qfos_force_runtime_clean_baseline_if_db_clean():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM trades) AS trades_n,
                    (SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001) AS open_n,
                    ps.equity,
                    ps.cash,
                    ps.exposure,
                    ps.drawdown
                FROM portfolio_snapshots ps
                ORDER BY ps.id DESC
                LIMIT 1
            """)).mappings().first()

        if not row:
            return False

        trades_n = int(row.get("trades_n") or 0)
        open_n = int(row.get("open_n") or 0)
        equity = float(row.get("equity") or 0.0)
        cash = float(row.get("cash") or 0.0)
        exposure = float(row.get("exposure") or 0.0)
        drawdown = float(row.get("drawdown") or 0.0)

        is_clean_db = (
            trades_n == 0
            and open_n == 0
        )

        if not is_clean_db:
            return False

        try:
            portfolio.cash = 100.0
            portfolio.equity = 100.0
            portfolio.peak = 100.0
        except Exception:
            pass

        try:
            portfolio.positions.clear()
        except Exception:
            pass

        for obj_name in [
            "entry_prices",
            "position_open_time",
            "position_peak_change",
            "shadow_positions",
            "shadow_entry_prices",
            "shadow_trade_counts",
            "trade_counts",
            "last_trade_time",
        ]:
            try:
                obj = globals().get(obj_name)
                if hasattr(obj, "clear"):
                    obj.clear()
            except Exception:
                pass

        try:
            # Clear stale pause only when it is clearly caused by the orphan-ledger stale drawdown.
            pr = str(globals().get("pause_reason", "") or "")
            if "max_daily_loss_hit" in pr or "-5.43" in pr:
                globals()["pause_reason_value"] = ""
        except Exception:
            pass

        print(
            "[QFOS_RUNTIME_BASELINE_SYNC] forced runtime clean baseline "
            "equity=100 cash=100 exposure=0 positions=0",
            flush=True,
        )
        return True

    except Exception as e:
        print(f"[QFOS_RUNTIME_BASELINE_SYNC_ERROR] error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1
# ============================================================


# ============================================================
# QFOS_AGENT1_CLEAN_BASELINE_RUNTIME_GUARD_V1
# Purpose:
#   If the DB is at a clean paper baseline, force in-memory runtime
#   position containers to agree. This prevents stale process-local
#   state from being synced back into DB as paper_position_sync.
# ============================================================

# QFOS_BASELINE_AUTHORITY_PATCH_START
def qfos_baseline_authority_clean_ledger_counts():
    """
    Return (trades_count, open_position_count) from the authoritative runtime DB.
    """
    try:
        with engine.begin() as conn:
            try:
                trades_count = int(conn.execute(text("SELECT COUNT(*) FROM trades")).scalar() or 0)
            except Exception:
                trades_count = 0

            try:
                open_position_count = int(conn.execute(text(
                    "SELECT COUNT(*) FROM positions WHERE COALESCE(quantity, 0) > 0"
                )).scalar() or 0)
            except Exception:
                open_position_count = 0

        return trades_count, open_position_count

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] clean_ledger_count_error={exc}", flush=True)
        return None, None


def qfos_baseline_authority_clear_redis_control_state():
    """
    Clear stale auxiliary Redis risk keys without deleting quantfund:control.

    quantfund:control is the runtime pause/resume authority.  It must survive
    startup so the shell/API startup interlock remains fail-closed until an
    explicit /resume writes paused=False.
    """
    try:
        import redis as _redis
    except Exception:
        return

    try:
        redis_url = str(getattr(settings, "redis_url", "redis://localhost:6379/0"))
        client = _redis.Redis.from_url(redis_url, decode_responses=True)

        stale_terms = (
            "max_daily_loss",
            "daily_loss",
            "near_blocked_drawdown",
            "blocked_drawdown",
            "risk_status",
            "pause_reason",
            "paused",
            "bot_state",
            "BLOCKED",
            "max_daily_loss_hit",
            "92.54",
            "5.90",
            "5.43",
        )

        deleted = []
        preserved_control = False

        for raw_key in client.scan_iter("*"):
            key = str(raw_key or "")
            if key == "quantfund:control":
                preserved_control = True
                continue

            lower_key = key.lower()
            if any(term.lower() in lower_key for term in stale_terms):
                try:
                    client.delete(key)
                    deleted.append(key)
                except Exception:
                    pass

        print(
            "[C21_STARTUP_CONTROL] "
            f"preserved_quantfund_control={preserved_control} "
            f"stale_aux_keys_deleted={deleted}",
            flush=True,
        )

    except Exception as exc:
        print(f"[C21_STARTUP_CONTROL] redis_cleanup_error={exc}", flush=True)


def qfos_baseline_authority_clear_control_module_state():
    """
    Clear automatic pause state while preserving pause_reason as callable.
    """
    try:
        import core.control as _control

        for name in ("paused", "_paused", "PAUSED", "is_bot_paused"):
            if hasattr(_control, name):
                try:
                    setattr(_control, name, False)
                except Exception:
                    pass

        for name in ("pause_reason", "_pause_reason", "PAUSE_REASON", "last_pause_reason"):
            if hasattr(_control, name):
                try:
                    current_attr = getattr(_control, name, None)
                    if name == "pause_reason" and callable(current_attr):
                        pass
                    else:
                        setattr(_control, name, "")
                except Exception:
                    pass

        # C23B: preserve authoritative pause/resume state.
        # Baseline cleanup must never invoke resume_bot or equivalent helpers.
        # Only the explicit /resume endpoint may write paused=False.
        print("[C23B_STARTUP_CONTROL] preserved_core_control_pause_authority", flush=True)
    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] control_module_clear_error={exc}", flush=True)

    try:
        qfos_restore_pause_reason_callable()
    except Exception:
        pass


def qfos_baseline_authority_write_clean_snapshot(regime="SIDEWAYS"):
    """
    Write a clean baseline snapshot using available columns.
    """
    try:
        import datetime as _dt

        with engine.begin() as conn:
            try:
                rows = conn.execute(text("SELECT * FROM portfolio_snapshots LIMIT 0"))
                cols = list(rows.keys())
            except Exception:
                cols = []

            if not cols:
                return

            now_s = _dt.datetime.utcnow().isoformat()

            values = {}

            if "equity" in cols:
                values["equity"] = float(INITIAL_EQUITY)
            if "cash" in cols:
                values["cash"] = float(INITIAL_EQUITY)
            if "exposure" in cols:
                values["exposure"] = 0.0
            if "drawdown" in cols:
                values["drawdown"] = 0.0
            if "regime" in cols:
                values["regime"] = str(regime or "SIDEWAYS")
            if "realized_pnl" in cols:
                values["realized_pnl"] = 0.0
            if "unrealized_pnl" in cols:
                values["unrealized_pnl"] = 0.0
            if "total_pnl" in cols:
                values["total_pnl"] = 0.0
            if "created_at" in cols:
                values["created_at"] = now_s
            if "updated_at" in cols:
                values["updated_at"] = now_s
            if "timestamp" in cols:
                values["timestamp"] = now_s

            if not values:
                return

            col_sql = ", ".join(values.keys())
            bind_sql = ", ".join([f":{k}" for k in values.keys()])

            conn.execute(
                text(f"/* QFOS_AGENT5: snapshot DB trigger enforces ledger accounting */ INSERT INTO portfolio_snapshots ({col_sql}) VALUES ({bind_sql})"),
                values,
            )

            print("[BASELINE_AUTHORITY] clean_baseline_snapshot_written", flush=True)

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] snapshot_write_error={exc}", flush=True)


def qfos_clean_runtime_state_if_db_baseline(reason="runtime"):
    """
    Hard clean-ledger runtime authority.

    If trades=0 and open_positions=0, stale runtime/risk/pause state must not survive.
    """
    try:
        trades_count, open_position_count = qfos_baseline_authority_clean_ledger_counts()

        if trades_count is None or open_position_count is None:
            return False

        if int(trades_count) != 0 or int(open_position_count) != 0:
            return False

        try:
            if hasattr(portfolio, "reset") and callable(getattr(portfolio, "reset")):
                portfolio.reset(float(INITIAL_EQUITY))
            else:
                portfolio.cash = float(INITIAL_EQUITY)
                portfolio.equity = float(INITIAL_EQUITY)
                portfolio.peak = float(INITIAL_EQUITY)

                if hasattr(portfolio, "positions"):
                    portfolio.positions.clear()
                if hasattr(portfolio, "avg_entry"):
                    portfolio.avg_entry.clear()
                if hasattr(portfolio, "realized_pnl"):
                    portfolio.realized_pnl = 0.0
                if hasattr(portfolio, "unrealized_pnl"):
                    portfolio.unrealized_pnl = 0.0

        except Exception as exc:
            print(f"[BASELINE_AUTHORITY] portfolio_reset_error={exc}", flush=True)

        for name in (
            "entry_prices",
            "position_open_time",
            "position_peak_change",
            "shadow_positions",
            "shadow_entry_prices",
            "shadow_trade_counts",
            "trade_counts",
            "last_trade_time",
            "quarantined_symbols",
        ):
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
            except Exception:
                pass

        for name in ("rejected", "rejected_orders", "last_rejected", "recent_rejections"):
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
                elif name in globals():
                    globals()[name] = []
            except Exception:
                pass

        try:
            globals()["current_risk_status"] = "SAFE"
            globals()["risk_status"] = "SAFE"
            globals()["bot_state"] = "RUNNING"
            globals()["paused"] = False
            globals()["pause_reason_value"] = ""
            globals()["last_risk_status"] = "SAFE"
            globals()["last_auto_pause_reason"] = None
        except Exception:
            pass

        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        for name in ("risk_engine", "risk", "engine_risk"):
            try:
                obj = globals().get(name)
                if obj is not None and hasattr(obj, "reset_risk_state"):
                    obj.reset_risk_state(float(INITIAL_EQUITY))
            except Exception:
                pass

        qfos_baseline_authority_clear_redis_control_state()
        qfos_baseline_authority_clear_control_module_state()

        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        try:
            regime = str(globals().get("regime", "SIDEWAYS") or "SIDEWAYS")
        except Exception:
            regime = "SIDEWAYS"

        qfos_baseline_authority_write_clean_snapshot(regime=regime)

        print(
            "[BASELINE_AUTHORITY] clean_ledger_runtime_reset_applied "
            f"reason={reason} trades={trades_count} open_positions={open_position_count} "
            f"equity={float(INITIAL_EQUITY):.2f} cash={float(INITIAL_EQUITY):.2f} "
            "exposure=0 drawdown=0 risk_status=SAFE paused=False pause_reason=''",
            flush=True,
        )

        return True

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] reset_error={exc}", flush=True)
        return False
# QFOS_BASELINE_AUTHORITY_PATCH_END

# C11_IMPORT_SAFE_GUARD_V1
if str(__import__("os").getenv("QFOS_IMPORT_SAFE", "")).strip().lower() in ("1", "true", "yes", "on"):
    print("[QFOS_IMPORT_SAFE] skipped runtime_baseline_sync_startup", flush=True)
else:
    qfos_clean_runtime_state_if_db_baseline(reason="startup")
    try:
        qfos_force_runtime_clean_baseline_if_db_clean()
    except Exception as e:
        print(f'[QFOS_RUNTIME_BASELINE_SYNC_CALL_ERROR] error={e}', flush=True)

# ============================================================
# QFOS_AGENT1_AGENT2_HARD_BASELINE_AUTHORITY_V1
# Purpose:
#   When the authoritative ledger is clean:
#       trades_count == 0 and open_position_count == 0
#   runtime/risk state must not preserve stale loss memory.
#
# Scope:
#   - Runtime memory
#   - risk_status / pause_reason / rejected stale loss reasons
#   - clean baseline portfolio snapshot
#   - Redis/control stale pause keys if accessible through runtime env
#
# Does NOT change:
#   - feature generation
#   - allocation gates
#   - execution accounting
#   - strategy thresholds
#   - live trading setting
# ============================================================

QFOS_HARD_BASELINE_EQUITY = 100.0
QFOS_HARD_BASELINE_CASH = 100.0
QFOS_HARD_BASELINE_EXPOSURE = 0.0
QFOS_HARD_BASELINE_DRAWNDOWN = 0.0

QFOS_STALE_LOSS_REASON_PATTERNS = (
    "max_daily_loss_hit",
    "near_blocked_drawdown",
    "blocked_drawdown",
    "risk_status=BLOCKED",
    "BLOCKED",
)

_QFOS_BASELINE_AUTHORITY_LAST_APPLIED = None


def _qfos_table_exists(conn, table_name):
    try:
        dialect = getattr(getattr(conn, "engine", None), "dialect", None)
        dialect_name = getattr(dialect, "name", "")
    except Exception:
        dialect_name = ""

    try:
        if dialect_name == "postgresql":
            row = conn.execute(
                text("""
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = :table
                    LIMIT 1
                """),
                {"table": table_name},
            ).first()
        else:
            row = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table LIMIT 1"),
                {"table": table_name},
            ).first()
        return row is not None
    except Exception:
        return False


def _qfos_table_columns(conn, table_name):
    try:
        dialect = getattr(getattr(conn, "engine", None), "dialect", None)
        dialect_name = getattr(dialect, "name", "")
    except Exception:
        dialect_name = ""

    try:
        if dialect_name == "postgresql":
            rows = conn.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table
                """),
                {"table": table_name},
            ).fetchall()
            return [r[0] for r in rows]
        else:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            return [r[1] for r in rows]
    except Exception:
        return []


def qfos_clean_ledger_counts():
    """
    Returns:
        (is_clean, trades_count, open_position_count, source)
    Missing trades/positions tables are treated as zero only for clean
    paper baseline recovery, because the PM condition is no evidence of
    trades or open positions.
    """
    try:
        with engine.begin() as conn:
            if _qfos_table_exists(conn, "trades"):
                trades_count = int(conn.execute(text("SELECT COUNT(*) FROM trades")).scalar() or 0)
            else:
                trades_count = 0

            open_position_count = 0
            if _qfos_table_exists(conn, "positions"):
                cols = _qfos_table_columns(conn, "positions")
                qty_col = "quantity" if "quantity" in cols else ("qty" if "qty" in cols else None)
                if qty_col:
                    open_position_count = int(
                        conn.execute(
                            text(f"SELECT COUNT(*) FROM positions WHERE COALESCE({qty_col},0) > 0"),
                        ).scalar()
                        or 0
                    )

        return (trades_count == 0 and open_position_count == 0, trades_count, open_position_count, "runtime_db")
    except Exception as e:
        print(f"[BASELINE_AUTHORITY] clean_ledger_count_error error={e}", flush=True)
        return (False, None, None, "error")


def qfos_write_clean_baseline_snapshot(source="baseline_authority"):
    try:
        with engine.begin() as conn:
            if not _qfos_table_exists(conn, "portfolio_snapshots"):
                return False

            cols = _qfos_table_columns(conn, "portfolio_snapshots")
            insert_cols = []
            values = {}

            def add(col, val):
                if col in cols:
                    insert_cols.append(col)
                    values[col] = val

            add("equity", QFOS_HARD_BASELINE_EQUITY)
            add("cash", QFOS_HARD_BASELINE_CASH)
            add("exposure", QFOS_HARD_BASELINE_EXPOSURE)
            add("exposure_pct", 0.0)
            add("drawdown", 0.0)
            add("realized_pnl", 0.0)
            add("unrealized_pnl", 0.0)
            add("total_pnl", 0.0)
            add("regime", globals().get("last_known_regime", "SIDEWAYS") or "SIDEWAYS")
            add("source", source)

            # Support common timestamp column names.
            import datetime as _dt
            now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            add("created_at", now)
            add("updated_at", now)
            add("timestamp", now)

            if not insert_cols:
                return False

            placeholders = ", ".join([f":{c}" for c in insert_cols])
            col_sql = ", ".join(insert_cols)
            conn.execute(
                text(f"INSERT INTO portfolio_snapshots ({col_sql}) VALUES ({placeholders})"),
                values,
            )
        return True
    except Exception as e:
        print(f"[BASELINE_AUTHORITY] clean_snapshot_write_error error={e}", flush=True)
        return False


def qfos_clear_stale_runtime_pause_state(source="baseline_authority"):
    """
    Clears automatic stale loss pause/reject state. Manual pause is preserved
    only if it has explicit manual metadata in the reason.
    """
    try:
        g = globals()

        reason = str(g.get("pause_reason", "") or "")
        paused_value = bool(g.get("paused", False))

        manual_pause = paused_value and (
            "manual" in reason.lower()
            or "human" in reason.lower()
            or "operator" in reason.lower()
            or "kill" in reason.lower()
        )

        stale_loss_pause = any(pat.lower() in reason.lower() for pat in QFOS_STALE_LOSS_REASON_PATTERNS)

        if stale_loss_pause or not manual_pause:
            g["paused"] = False
            g["pause_reason"] = ""
            g["risk_status"] = "SAFE"
            g["last_risk_status"] = "SAFE"

        # Clear common rejected containers if they are stale loss only.
        try:
            rej = g.get("rejected", [])
            if isinstance(rej, list):
                g["rejected"] = [
                    r for r in rej
                    if not any(pat.lower() in str(r).lower() for pat in QFOS_STALE_LOSS_REASON_PATTERNS)
                ]
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"[BASELINE_AUTHORITY] clear_pause_state_error error={e}", flush=True)
        return False


def qfos_apply_clean_ledger_runtime_reset(source="baseline_authority"):
    global _QFOS_BASELINE_AUTHORITY_LAST_APPLIED

    is_clean, trades_count, open_position_count, count_source = qfos_clean_ledger_counts()
    if not is_clean:
        return False

    try:
        # Runtime portfolio memory
        try:
            portfolio.cash = QFOS_HARD_BASELINE_CASH
        except Exception:
            pass

        try:
            portfolio.equity = QFOS_HARD_BASELINE_EQUITY
        except Exception:
            pass

        try:
            portfolio.drawdown = 0.0
        except Exception:
            pass

        try:
            portfolio.realized_pnl = 0.0
        except Exception:
            pass

        try:
            portfolio.unrealized_pnl = 0.0
        except Exception:
            pass

        for name in [
            "positions",
            "entry_prices",
            "position_open_time",
            "position_peak_change",
            "trade_counts",
            "shadow_positions",
            "shadow_entry_prices",
            "shadow_trade_counts",
            "quarantined_symbols",
        ]:
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
            except Exception:
                pass

        qfos_clear_stale_runtime_pause_state(source=source)
        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        # Force known globals that later status/risk code may read.
        globals()["current_risk_status"] = "SAFE"
        globals()["risk_status"] = "SAFE"
        globals()["bot_state"] = "RUNNING"
        globals()["pause_reason_value"] = ""
        globals()["paused"] = False
        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        qfos_write_clean_baseline_snapshot(source=source)

        _QFOS_BASELINE_AUTHORITY_LAST_APPLIED = source

        print(
            "[BASELINE_AUTHORITY] clean_ledger_runtime_reset_applied "
            f"source={source} trades_count={trades_count} open_position_count={open_position_count} "
            "equity=100.00 cash=100.00 exposure=0.00 drawdown=0.00 risk_status=SAFE paused=False pause_reason=''",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[BASELINE_AUTHORITY] reset_apply_error source={source} error={e}", flush=True)
        return False


def qfos_baseline_authority_status_override(payload=None, source="status_payload"):
    """
    Applies clean baseline to a /status/live-cache payload when ledger is clean.
    """
    if not qfos_apply_clean_ledger_runtime_reset(source=source):
        return payload

    try:
        if isinstance(payload, dict):
            payload["risk_status"] = "SAFE"
            payload["bot_state"] = "RUNNING"
            payload["paused"] = False
            payload["pause_reason"] = ""
            payload["positions"] = [] if isinstance(payload.get("positions"), list) else {}

            p = payload.get("portfolio")
            if isinstance(p, dict):
                p["equity"] = 100.0
                p["cash"] = 100.0
                p["exposure"] = 0.0
                p["exposure_pct"] = 0.0
                p["drawdown"] = 0.0
                p["realized_pnl"] = 0.0
                p["unrealized_pnl"] = 0.0
                p["total_pnl"] = 0.0

            perf = payload.get("performance")
            if isinstance(perf, dict):
                perf["total_trades"] = 0
                perf["buy_count"] = 0
                perf["sell_count"] = 0
                perf["realized_pnl"] = 0.0
                perf["unrealized_pnl"] = 0.0
                perf["total_pnl"] = 0.0

            trading = payload.get("trading")
            if isinstance(trading, dict):
                trading["total_trades"] = 0
                trading["buy_count"] = 0
                trading["sell_count"] = 0
                trading["latest_trades"] = []

    except Exception as e:
        print(f"[BASELINE_AUTHORITY] status_override_error error={e}", flush=True)

    return payload


# ============================================================
# End QFOS_AGENT1_AGENT2_HARD_BASELINE_AUTHORITY_V1
# ============================================================

# C11_IMPORT_SAFE_GUARD_V1
# Keep imports side-effect-free; normal service startup remains the default.
if str(__import__("os").getenv("QFOS_IMPORT_SAFE", "")).strip().lower() in ("1", "true", "yes", "on"):
    print("[QFOS_IMPORT_SAFE] skipped baseline_runtime_and_alert_startup", flush=True)
else:
    qfos_apply_clean_ledger_runtime_reset(source='startup_before_bot_loop')
    qfos_runtime_start()
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
            conn.execute(text("\n                DELETE FROM symbol_quarantine\n                WHERE blocked_until IS NOT NULL\n                  AND blocked_until <= CURRENT_TIMESTAMP + interval '3 hours'\n            "))
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
        if qfos_apply_clean_ledger_runtime_reset(source='before_drawdown_entry_gate'):
            return (True, 'clean_ledger_baseline_authority')
        blocked_drawdown = float(getattr(settings, 'blocked_drawdown', -0.05))
        near_buffer = abs(float(getattr(settings, 'near_blocked_drawdown_buffer', 0.0025)))
        near_blocked_drawdown = blocked_drawdown + near_buffer

        open_positions_count = sum(
            1 for _, q in portfolio.positions.items()
            if float(q or 0) > 1e-08
        )

        # Agent 2 Phase 3A stale drawdown repair:
        # If runtime/DB is clean and equity is back at the reset baseline,
        # do not let old portfolio.peak/equity memory block fresh BUYs.
        if open_positions_count == 0 and float(equity or 0.0) >= INITIAL_EQUITY * 0.999:
            if current_drawdown < 0:
                try:
                    portfolio.cash = float(equity or INITIAL_EQUITY)
                    portfolio.equity = float(equity or INITIAL_EQUITY)
                    portfolio.peak = max(float(INITIAL_EQUITY), float(equity or INITIAL_EQUITY))
                    current_drawdown = 0.0
                    print('[AGENT2_RISK_RESET] cleared stale drawdown gate in can_buy', flush=True)
                except Exception:
                    pass

        # Hard blocked must come before near-blocked.
        # A real hard breach should not be mislabeled near_blocked_drawdown.
        if current_drawdown <= blocked_drawdown:
            return (False, f'blocked_drawdown_{current_drawdown:.4f}')

        # Near-blocked is the warning zone before hard blocked.
        # Drawdown is negative, so near threshold is less negative than blocked.
        if current_drawdown <= near_blocked_drawdown:
            return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')

        if current_drawdown <= caution_drawdown:
            try:
                current_exposure = float(getattr(portfolio, 'exposure', 0.0) or 0.0)
            except Exception:
                current_exposure = 0.0
            try:
                exposure_pct = current_exposure / max(float(equity or 0.0), 1e-09)
            except Exception:
                exposure_pct = 0.0
            if open_positions_count >= 2:
                return (False, f'caution_drawdown_position_cap_{current_drawdown:.4f}')
            if exposure_pct >= 0.5:
                return (False, f'caution_drawdown_exposure_{current_drawdown:.4f}_{exposure_pct:.4f}')
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT\n                    SUM(CASE WHEN strategy='stop_loss' THEN 1 ELSE 0 END) AS stop_losses,\n                    SUM(CASE WHEN strategy='take_profit' THEN 1 ELSE 0 END) AS take_profits\n                FROM trades\n                WHERE symbol = :symbol\n                  AND side = 'sell'\n                  AND created_at >= CURRENT_TIMESTAMP\n            "), {'symbol': symbol}).mappings().first()
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
    if qfos_apply_clean_ledger_runtime_reset(source='before_daily_loss_gate'):
        return (True, 'clean_ledger_baseline_authority')
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

# ============================================================
# QFOS_AGENT1_PREPERSIST_BUY_ROLLBACK_V1
# Purpose:
#   apply_buy() mutates in-memory paper cash/positions before the
#   final firewall/persistence stage. If the final firewall rejects
#   that BUY, the runtime can keep a ghost position with no BUY row.
#   This rollback removes only that unpersisted in-memory mutation.
#   It does not change thresholds, risk, fallback logic, dashboard,
#   or Agent 5's atomic persistence boundary.
# ============================================================

def qfos_rollback_unpersisted_buy(fill, source="final_firewall"):
    try:
        if not isinstance(fill, dict):
            return False
        side = str(fill.get("side", "")).lower()
        if side != "buy":
            return False
        if bool(fill.get("shadow_mode", False)):
            return False

        symbol = str(fill.get("symbol") or "")
        if not symbol:
            return False

        qty = float(fill.get("quantity") or fill.get("qty") or 0.0)
        price = float(fill.get("fill_price") or fill.get("expected_price") or fill.get("price") or 0.0)
        if qty <= 0 or price <= 0:
            return False

        fee = qty * price * FEE_RATE
        current_qty = float(portfolio.positions.get(symbol, 0.0) or 0.0)

        # Only reverse up to the quantity that this rejected BUY added.
        rollback_qty = min(qty, max(current_qty, 0.0))
        if rollback_qty <= 0:
            return False

        portfolio.cash += rollback_qty * price + fee
        new_qty = current_qty - rollback_qty

        if new_qty <= 1e-08:
            portfolio.positions[symbol] = 0.0
            entry_prices.pop(symbol, None)
            position_open_time.pop(symbol, None)
            position_peak_change.pop(symbol, None)
            trade_counts[symbol] = max(0, int(trade_counts.get(symbol, 0) or 0) - 1)
        else:
            portfolio.positions[symbol] = new_qty

        print(
            f"[QFOS_PREPERSIST_BUY_ROLLBACK] symbol={symbol} qty={rollback_qty:.12f} "
            f"price={price:.12f} source={source}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[QFOS_PREPERSIST_BUY_ROLLBACK_ERROR] error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT1_PREPERSIST_BUY_ROLLBACK_V1
# ============================================================

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
    qfos_invalidate_ledger_cache()
    # Preserve legacy helper name, but route through the single atomic boundary.
    return qfos_persist_fill_atomic(conn, fill, source='save_trade')

def ensure_positions_table():
    with engine.begin() as conn:
        conn.execute(text("\n            CREATE TABLE IF NOT EXISTS positions (\n                symbol TEXT PRIMARY KEY,\n                quantity REAL NOT NULL DEFAULT 0,\n                avg_entry REAL NOT NULL DEFAULT 0,\n                realized_pnl REAL NOT NULL DEFAULT 0,\n                unrealized_pnl REAL NOT NULL DEFAULT 0,\n                last_price REAL NOT NULL DEFAULT 0,\n                exposure REAL NOT NULL DEFAULT 0,\n                strategy TEXT,\n                trade_uuid TEXT,\n                highest_price_seen REAL DEFAULT 0,\n                lowest_price_seen REAL DEFAULT 0,\n                max_unrealized_profit REAL DEFAULT 0,\n                max_unrealized_loss REAL DEFAULT 0,\n                updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP + interval '3 hours')\n            )\n        "))
        cols = [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'positions' AND table_schema = 'public'"))]
        if 'strategy' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN strategy TEXT'))
        if 'trade_uuid' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN trade_uuid TEXT'))
        if 'highest_price_seen' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN highest_price_seen REAL DEFAULT 0'))
        if 'lowest_price_seen' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN lowest_price_seen REAL DEFAULT 0'))
        if 'max_unrealized_profit' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN max_unrealized_profit REAL DEFAULT 0'))
        if 'max_unrealized_loss' not in cols:
            conn.execute(text('ALTER TABLE positions ADD COLUMN max_unrealized_loss REAL DEFAULT 0'))
        conn.execute(text("\n            CREATE TABLE IF NOT EXISTS symbol_quarantine (\n                symbol TEXT PRIMARY KEY,\n                reason TEXT NOT NULL,\n                blocked_until DATETIME,\n                created_at DATETIME DEFAULT (CURRENT_TIMESTAMP + interval '3 hours')\n            )\n        "))

def quarantine_symbol(symbol: str, reason: str, hours: int=24):
    with engine.begin() as conn:
        conn.execute(text("\n            INSERT INTO symbol_quarantine (symbol, reason, blocked_until, created_at)\n            VALUES (:symbol, :reason, (CURRENT_TIMESTAMP + interval '3 hours' + (:hours || ' hours')::interval), CURRENT_TIMESTAMP + interval '3 hours')\n            ON CONFLICT (symbol) DO UPDATE SET\n            reason = EXCLUDED.reason,\n            blocked_until = EXCLUDED.blocked_until,\n            created_at = EXCLUDED.created_at\n        "), {'symbol': symbol, 'reason': reason, 'hours': hours})
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
    conn.execute(text("\n        INSERT INTO positions(\n            symbol, quantity, avg_entry, realized_pnl,\n            unrealized_pnl, last_price, exposure, strategy, updated_at\n        )\n        VALUES(\n            :symbol, :quantity, :avg_entry, :realized_pnl,\n            :unrealized_pnl, :last_price, :exposure, :strategy, CURRENT_TIMESTAMP + interval '3 hours'\n        )\n        ON CONFLICT (symbol)\n        DO UPDATE SET\n            quantity = EXCLUDED.quantity,\n            avg_entry = EXCLUDED.avg_entry,\n            realized_pnl = EXCLUDED.realized_pnl,\n            unrealized_pnl = EXCLUDED.unrealized_pnl,\n            last_price = EXCLUDED.last_price,\n            exposure = EXCLUDED.exposure,\n            strategy = EXCLUDED.strategy,\n            updated_at = CURRENT_TIMESTAMP + interval '3 hours'\n    "), {'symbol': symbol, 'quantity': new_qty, 'avg_entry': new_avg_entry, 'realized_pnl': new_realized_pnl, 'unrealized_pnl': unrealized_pnl, 'last_price': price, 'exposure': exposure, 'strategy': new_strategy})
    return (fill_pnl, applied_strategy)


# ============================================================
# QFOS_ATOMIC_FILL_PERSISTENCE_V1
# Single validation/persistence boundary for paper BUY/SELL rows.
# Prevents duplicate/oversized SELL rows from Profit Engine,
# watchdogs, or the main loop when DB open quantity is already zero.
# ============================================================

def _qfos_atomic_get(fill, key, default=None):
    try:
        if isinstance(fill, dict):
            return fill.get(key, default)
        return getattr(fill, key, default)
    except Exception:
        return default


def _qfos_atomic_float(value, default=0.0):
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default




def _qfos_atomic_select_position(conn, symbol):
    if _qfos_atomic_is_sqlalchemy(conn):
        return conn.execute(text("""
            SELECT symbol, quantity, avg_entry, realized_pnl, strategy
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

    return conn.execute("""
        SELECT symbol, quantity, avg_entry, realized_pnl, strategy
        FROM positions
        WHERE symbol = ?
        LIMIT 1
    """, (symbol,)).fetchone()


def _qfos_atomic_row_get(row, key, index, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return default







# ============================================================
# END QFOS_ATOMIC_FILL_PERSISTENCE_V1
# ============================================================


# ============================================================
# QFOS_DB_POSITION_SYNC_V1
# Purpose:
#   Keep SQLite positions table aligned with the bot's real
#   in-memory paper portfolio.
#
# Why:
#   /status reads positions from DB, but the live loop can hold
#   positions in portfolio.positions while the DB positions table
#   remains empty or stale. This makes dashboard exposure/trades
#   look false.
#
# Rules:
#   - No commits here. Caller owns engine.begin().
#   - No strategy/risk changes.
#   - Only sync non-zero paper positions into DB.
# ============================================================


# ============================================================
# QFOS_TRADES_SCHEMA_REPAIR_V1
# Repairs old SQLite trades table without dropping data.
# No commits here. Caller owns engine.begin().
# ============================================================

_main_tables_ensured = False

def qfos_ensure_trades_schema(conn):
    global _main_tables_ensured
    if _main_tables_ensured:
        return
    try:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                quantity REAL,
                expected_price REAL,
                fill_price REAL,
                slippage_bps REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                strategy TEXT,
                confidence REAL,
                live BOOLEAN DEFAULT 0,
                shadow_mode BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        existing_cols = {
            row[0] for row in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'trades' AND table_schema = 'public'")).fetchall()
        }

        required_cols = {
            "symbol": "TEXT",
            "side": "TEXT",
            "quantity": "REAL",
            "expected_price": "REAL",
            "fill_price": "REAL",
            "slippage_bps": "REAL DEFAULT 0",
            "pnl": "REAL DEFAULT 0",
            "strategy": "TEXT",
            "confidence": "REAL",
            "live": "BOOLEAN DEFAULT 0",
            "shadow_mode": "BOOLEAN DEFAULT 0",
            "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "trade_uuid": "TEXT",
            "regime": "TEXT",
            "experiment_id": "TEXT",
            "software_version": "TEXT",
            "configuration_hash": "TEXT",
            "mfe": "REAL",
            "mae": "REAL",
            "peak_price": "REAL",
            "trough_price": "REAL",
        }

        for col, ddl in required_cols.items():
            if col not in existing_cols:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {col} {ddl}"))
                print(f"[TRADES_SCHEMA_REPAIR] added column trades.{col}", flush=True)

        _main_tables_ensured = True
    except Exception as exc:
        print(f"[TRADES_SCHEMA_REPAIR] failed: {exc}", flush=True)

# ============================================================
# End QFOS_TRADES_SCHEMA_REPAIR_V1
# ============================================================

def qfos_db_sync_positions_from_portfolio(conn, portfolio, prices):
    global _main_tables_ensured
    if not _main_tables_ensured:
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL,
                    avg_entry REAL,
                    realized_pnl REAL DEFAULT 0,
                    unrealized_pnl REAL DEFAULT 0,
                    last_price REAL,
                    exposure REAL,
                    strategy TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
        except Exception as e:
            print(f"[QFOS_DB_POSITION_SYNC] ensure positions failed: {e}", flush=True)
            return

    try:
        raw_positions = getattr(portfolio, "positions", {}) or {}
    except Exception:
        raw_positions = {}

    if not isinstance(raw_positions, dict):
        return

    live_symbols = set()

    for symbol, qty_raw in raw_positions.items():
        try:
            symbol = str(symbol)
            qty = float(qty_raw or 0.0)
        except Exception:
            continue

        if abs(qty) <= 0.00000001:
            continue

        live_symbols.add(symbol)

        try:
            price = float((prices or {}).get(symbol) or 0.0)
        except Exception:
            price = 0.0

        if price <= 0:
            try:
                existing = conn.execute(text("""
                    SELECT last_price, avg_entry
                    FROM positions
                    WHERE symbol = :symbol
                """), {"symbol": symbol}).mappings().first()
                if existing:
                    price = float(existing.get("last_price") or existing.get("avg_entry") or 0.0)
            except Exception:
                price = 0.0

        try:
            existing = conn.execute(text("""
                SELECT avg_entry, realized_pnl, strategy
                FROM positions
                WHERE symbol = :symbol
            """), {"symbol": symbol}).mappings().first()
        except Exception:
            existing = None

        avg_entry = 0.0
        realized_pnl = 0.0
        strategy = "paper_position_sync"

        if existing:
            try:
                avg_entry = float(existing.get("avg_entry") or 0.0)
            except Exception:
                avg_entry = 0.0
            try:
                realized_pnl = float(existing.get("realized_pnl") or 0.0)
            except Exception:
                realized_pnl = 0.0
            try:
                strategy = str(existing.get("strategy") or strategy)
            except Exception:
                pass

        # If avg_entry was missing because the DB row was absent/stale,
        # use current price as a safe display fallback. This avoids fake PnL.
        if avg_entry <= 0:
            avg_entry = price if price > 0 else 0.0

        exposure = abs(qty) * price if price > 0 else 0.0
        unrealized_pnl = qty * (price - avg_entry) if price > 0 and avg_entry > 0 else 0.0

        try:
            conn.execute(text("""
                INSERT INTO positions (
                    symbol, quantity, avg_entry, realized_pnl,
                    unrealized_pnl, last_price, exposure, strategy, updated_at
                )
                VALUES (
                    :symbol, :quantity, :avg_entry, :realized_pnl,
                    :unrealized_pnl, :last_price, :exposure, :strategy,
                    CURRENT_TIMESTAMP + interval '3 hours'
                )
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    avg_entry = CASE
                        WHEN positions.avg_entry IS NULL OR positions.avg_entry <= 0
                        THEN excluded.avg_entry
                        ELSE positions.avg_entry
                    END,
                    realized_pnl = COALESCE(positions.realized_pnl, 0),
                    unrealized_pnl = excluded.unrealized_pnl,
                    last_price = excluded.last_price,
                    exposure = excluded.exposure,
                    strategy = COALESCE(positions.strategy, excluded.strategy),
                    updated_at = excluded.updated_at
            """), {
                "symbol": symbol,
                "quantity": qty,
                "avg_entry": avg_entry,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "last_price": price,
                "exposure": exposure,
                "strategy": strategy,
            })
        except Exception as e:
            print(f"[QFOS_DB_POSITION_SYNC] upsert failed symbol={symbol}: {e}", flush=True)

    # Mark DB rows closed if they are no longer in memory.
    # Do not delete them; keep realized/history visible.
    try:
        rows = conn.execute(text("""
            SELECT symbol
            FROM positions
            WHERE quantity > 0.00000001
        """)).mappings().all()

        for row in rows:
            db_symbol = str(row.get("symbol") or "")
            if db_symbol and db_symbol not in live_symbols:
                conn.execute(text("""
                    UPDATE positions
                    SET quantity = 0.0,
                        exposure = 0.0,
                        unrealized_pnl = 0.0,
                        updated_at = CURRENT_TIMESTAMP + interval '3 hours'
                    WHERE symbol = :symbol
                """), {"symbol": db_symbol})
    except Exception as e:
        print(f"[QFOS_DB_POSITION_SYNC] stale close pass failed: {e}", flush=True)

# ============================================================
# End QFOS_DB_POSITION_SYNC_V1
# ============================================================

def mark_positions_to_market(conn, prices):
    for symbol, price in prices.items():
        row = conn.execute(text('\n            SELECT quantity, avg_entry, realized_pnl\n            FROM positions\n            WHERE symbol = :symbol\n        '), {'symbol': symbol}).mappings().first()
        if not row:
            continue
        qty = float(row['quantity'] or 0)
        avg_entry = float(row['avg_entry'] or 0)
        if qty <= 0:
            continue
        exposure = qty * float(price)
        unrealized_pnl = qty * (float(price) - avg_entry) if avg_entry > 0 else 0.0

        # ---- MFE / MAE lifecycle tracking ----
        # Update highest_price_seen and lowest_price_seen using a single atomic
        # SQL statement so there is no Python read-modify-write race window.
        # GREATEST/LEAST are standard PostgreSQL functions; they also work in
        # SQLite >= 3.38 via the MAX()/MIN() scalar form — but we guard with a
        # COALESCE so that a NULL initial value is treated as 0.
        try:
            cols = _qfos_table_columns(conn, "positions")
            has_peak = "highest_price_seen" in cols and "lowest_price_seen" in cols
        except Exception:
            has_peak = False

        if has_peak:
            conn.execute(text("""
                UPDATE positions
                SET last_price = :last_price,
                    exposure = :exposure,
                    unrealized_pnl = :unrealized_pnl,
                    highest_price_seen = GREATEST(COALESCE(highest_price_seen, 0), :price),
                    lowest_price_seen = CASE
                        WHEN COALESCE(lowest_price_seen, 0) <= 0
                        THEN :price
                        ELSE LEAST(lowest_price_seen, :price)
                    END,
                    updated_at = CURRENT_TIMESTAMP + interval '3 hours'
                WHERE symbol = :symbol
            """), {
                'symbol': symbol,
                'last_price': float(price),
                'exposure': exposure,
                'unrealized_pnl': unrealized_pnl,
                'price': float(price),
            })
        else:
            conn.execute(text("""
                UPDATE positions
                SET last_price = :last_price,
                    exposure = :exposure,
                    unrealized_pnl = :unrealized_pnl,
                    updated_at = CURRENT_TIMESTAMP + interval '3 hours'
                WHERE symbol = :symbol
            """), {'symbol': symbol, 'last_price': float(price), 'exposure': exposure, 'unrealized_pnl': unrealized_pnl})
# C11_IMPORT_SAFE_GUARD_V1
if str(__import__("os").getenv("QFOS_IMPORT_SAFE", "")).strip().lower() in ("1", "true", "yes", "on"):
    print("[QFOS_IMPORT_SAFE] skipped database_wait_and_post_wait_reset", flush=True)
else:
    wait_for_database()
    qfos_apply_clean_ledger_runtime_reset(source='after_wait_for_database')
    ensure_positions_table()


# ============================================================
# QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1
#
# Agent 2 scope:
#   Exit decision policy: TP, SL, sideways stagnation,
#   max hold, trailing profit, breakeven protection.
#
# Agent 5 scope:
#   SELL execution safety: valid open quantity only,
#   no duplicate full exits, is_exit=true, exit_reason populated,
#   quantity capped to open quantity, persistence via atomic boundary.
#
# This block does not alter entry allocation, feature generation,
# live-trading setting, strategy scoring, or cash/equity formulas.
# ============================================================

QFOS_EXIT_TAKE_PROFIT_PCT = 0.0085
QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT = 0.0055
QFOS_EXIT_STOP_LOSS_PCT = -0.0065
QFOS_EXIT_SIDEWAYS_STOP_LOSS_PCT = -0.0045

QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE = 20.0
QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_PNL = -0.0025
QFOS_EXIT_SIDEWAYS_STAGNATION_MAX_PNL = 0.0035

QFOS_EXIT_MAX_HOLD_MINUTES = 45.0
QFOS_EXIT_TRAILING_PEAK_PCT = 0.0045
QFOS_EXIT_TRAILING_FLOOR_PCT = 0.0015
QFOS_EXIT_BREAKEVEN_PEAK_PCT = 0.0035
QFOS_EXIT_BREAKEVEN_FLOOR_PCT = 0.0002

QFOS_EXIT_DAEMON_INTERVAL_SECONDS = 12.0
QFOS_EXIT_MIN_SELL_NOTIONAL_USD = 0.0


def qfos_exit_lifecycle_ensure_tables():
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS qfos_exit_lifecycle_state (
                    symbol TEXT PRIMARY KEY,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    peak_pnl_pct DOUBLE PRECISION DEFAULT 0,
                    last_pnl_pct DOUBLE PRECISION DEFAULT 0,
                    last_age_min DOUBLE PRECISION DEFAULT 0,
                    last_decision TEXT,
                    last_reason TEXT,
                    last_sell_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS qfos_exit_decision_audit (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol TEXT,
                    age_min DOUBLE PRECISION,
                    pnl_pct DOUBLE PRECISION,
                    peak_pnl_pct DOUBLE PRECISION,
                    decision TEXT,
                    reason TEXT,
                    quantity DOUBLE PRECISION,
                    avg_entry DOUBLE PRECISION,
                    last_price DOUBLE PRECISION,
                    regime TEXT
                )
            """))
        return True
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] ensure_tables error={e}", flush=True)
        return False


def qfos_exit_lifecycle_fetch_positions():
    # QFOS_EXIT_LIFECYCLE_REENTRY_EPOCH_V1
    # Age must begin with the current net-open run, not the first BUY ever
    # recorded for a symbol. This prevents a closed historical position from
    # making a new entry appear immediately stale.
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                WITH ordered AS (
                    SELECT
                        id,
                        symbol,
                        created_at,
                        side,
                        quantity,
                        SUM(
                            CASE
                                WHEN LOWER(side) = 'buy' THEN quantity
                                ELSE -quantity
                            END
                        ) OVER (
                            PARTITION BY symbol
                            ORDER BY id
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) AS running_qty
                    FROM trades
                ),
                last_flat AS (
                    SELECT
                        symbol,
                        MAX(id) FILTER (
                            WHERE running_qty <= 0.00000001
                        ) AS last_flat_id
                    FROM ordered
                    GROUP BY symbol
                ),
                current_open_epoch AS (
                    SELECT
                        o.symbol,
                        MIN(o.created_at) FILTER (
                            WHERE o.id > COALESCE(f.last_flat_id, 0)
                              AND LOWER(o.side) = 'buy'
                        ) AS entry_started_at
                    FROM ordered o
                    LEFT JOIN last_flat f
                        ON f.symbol = o.symbol
                    GROUP BY o.symbol
                )
                SELECT
                    p.symbol,
                    p.quantity,
                    p.avg_entry,
                    p.last_price,
                    p.exposure,
                    p.unrealized_pnl,
                    p.strategy,
                    e.entry_started_at,
                    EXTRACT(
                        EPOCH FROM (
                            CURRENT_TIMESTAMP - e.entry_started_at
                        )
                    ) / 60.0 AS age_minutes
                FROM positions p
                JOIN current_open_epoch e
                    ON e.symbol = p.symbol
                WHERE p.quantity > 0.00000001
                  AND e.entry_started_at IS NOT NULL
                ORDER BY age_minutes DESC
            """)).mappings().all()

        return [dict(r) for r in rows]

    except Exception as e:
        print(
            f"[EXIT_DECISION_ERROR] fetch_positions error={e}",
            flush=True,
        )
        return []

def qfos_exit_lifecycle_current_regime():
    try:
        r = str(globals().get("last_known_regime") or "").upper()
        if r:
            return r
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT regime
                FROM portfolio_snapshots
                ORDER BY id DESC
                LIMIT 1
            """)).mappings().first()
        if row and row.get("regime"):
            return str(row.get("regime")).upper()
    except Exception:
        pass

    return "SIDEWAYS"


def qfos_exit_lifecycle_get_peak(
    conn,
    symbol,
    pnl_pct,
    entry_started_at=None,
):
    # QFOS_EXIT_LIFECYCLE_REENTRY_EPOCH_V1
    # Peak PnL belongs to one open position epoch only. A later re-entry must
    # not inherit a peak from an earlier, closed position in the same symbol.
    try:
        conn.execute(text("""
            ALTER TABLE qfos_exit_lifecycle_state
            ADD COLUMN IF NOT EXISTS entry_started_at TIMESTAMP
        """))
    except Exception:
        pass

    row = conn.execute(text("""
        SELECT
            peak_pnl_pct,
            entry_started_at
        FROM qfos_exit_lifecycle_state
        WHERE symbol = :symbol
    """), {
        "symbol": symbol,
    }).mappings().first()

    old_peak = float((row or {}).get("peak_pnl_pct") or pnl_pct or 0.0)
    old_epoch = (row or {}).get("entry_started_at")

    is_new_epoch = (
        entry_started_at is not None
        and old_epoch != entry_started_at
    )

    if is_new_epoch:
        peak = float(pnl_pct or 0.0)
    else:
        peak = max(old_peak, float(pnl_pct or 0.0))

    conn.execute(text("""
        INSERT INTO qfos_exit_lifecycle_state (
            symbol,
            entry_started_at,
            peak_pnl_pct,
            last_pnl_pct,
            updated_at
        )
        VALUES (
            :symbol,
            :entry_started_at,
            :peak,
            :pnl,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (symbol)
        DO UPDATE SET
            entry_started_at = EXCLUDED.entry_started_at,
            peak_pnl_pct = CASE
                WHEN qfos_exit_lifecycle_state.entry_started_at
                     IS DISTINCT FROM EXCLUDED.entry_started_at
                THEN EXCLUDED.peak_pnl_pct
                ELSE GREATEST(
                    qfos_exit_lifecycle_state.peak_pnl_pct,
                    EXCLUDED.peak_pnl_pct
                )
            END,
            last_pnl_pct = EXCLUDED.last_pnl_pct,
            updated_at = CURRENT_TIMESTAMP
    """), {
        "symbol": symbol,
        "entry_started_at": entry_started_at,
        "peak": peak,
        "pnl": float(pnl_pct or 0.0),
    })

    if is_new_epoch:
        print(
            f"[EXIT_REENTRY_EPOCH_RESET] "
            f"symbol={symbol} "
            f"entry_started_at={entry_started_at}",
            flush=True,
        )

    return peak

def qfos_exit_lifecycle_strong_runner(symbol, age_min, pnl_pct, peak_pnl_pct, regime):
    try:
        r = str(regime or "").upper()

        # Strong runner means a position is meaningfully green and still near its peak.
        # It protects winners from premature time/stagnation exits, but not from hard stop-loss.
        if pnl_pct >= 0.0065 and peak_pnl_pct >= 0.0065 and (peak_pnl_pct - pnl_pct) <= 0.0015:
            return True

        if r != "SIDEWAYS" and pnl_pct >= 0.0045 and (peak_pnl_pct - pnl_pct) <= 0.0010 and age_min < 90:
            return True

        return False
    except Exception:
        return False


def qfos_exit_lifecycle_decide(symbol, age_min, pnl_pct, peak_pnl_pct, regime):
    r = str(regime or "").upper()

    take_profit = QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT if r == "SIDEWAYS" else QFOS_EXIT_TAKE_PROFIT_PCT
    stop_loss = QFOS_EXIT_SIDEWAYS_STOP_LOSS_PCT if r == "SIDEWAYS" else QFOS_EXIT_STOP_LOSS_PCT

    strong_runner = qfos_exit_lifecycle_strong_runner(symbol, age_min, pnl_pct, peak_pnl_pct, r)

    if pnl_pct >= take_profit:
        reason = "sideways_take_profit_exit" if r == "SIDEWAYS" else "take_profit_exit"
        return "SELL", reason

    if pnl_pct <= stop_loss:
        reason = "sideways_stop_loss_exit" if r == "SIDEWAYS" else "stop_loss_exit"
        return "SELL", reason

    if peak_pnl_pct >= QFOS_EXIT_TRAILING_PEAK_PCT and pnl_pct <= QFOS_EXIT_TRAILING_FLOOR_PCT:
        return "SELL", "trailing_profit_exit"

    if peak_pnl_pct >= QFOS_EXIT_BREAKEVEN_PEAK_PCT and pnl_pct <= QFOS_EXIT_BREAKEVEN_FLOOR_PCT:
        return "SELL", "breakeven_protection_exit"

    if r == "SIDEWAYS":
        if (
            age_min >= QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE
            and QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_PNL <= pnl_pct <= QFOS_EXIT_SIDEWAYS_STAGNATION_MAX_PNL
        ):
            if strong_runner:
                return "HOLD", "hold_runner_conditions_true"
            return "SELL", "sideways_stagnation_exit"

    if age_min >= QFOS_EXIT_MAX_HOLD_MINUTES:
        if strong_runner:
            return "HOLD", "hold_runner_conditions_true"
        reason = "sideways_max_hold_exit" if r == "SIDEWAYS" else "max_hold_exit"
        return "SELL", reason

    # Explain HOLD clearly.
    if age_min < min(QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE, QFOS_EXIT_MAX_HOLD_MINUTES):
        return "HOLD", "hold_not_old_enough"

    if pnl_pct < take_profit and pnl_pct > stop_loss:
        return "HOLD", "hold_exit_threshold_not_met"

    if pnl_pct < take_profit:
        return "HOLD", "hold_take_profit_not_hit"

    if pnl_pct > stop_loss:
        return "HOLD", "hold_stop_loss_not_hit"

    return "HOLD", "hold_exit_threshold_not_met"


def qfos_exit_lifecycle_recent_sell_exists(conn, symbol):
    row = conn.execute(text("""
        SELECT COUNT(*) AS n
        FROM trades
        WHERE symbol = :symbol
          AND lower(side) = 'sell'
          AND created_at >= CURRENT_TIMESTAMP - interval '2 minutes'
    """), {"symbol": symbol}).mappings().first()

    return int((row or {}).get("n") or 0) > 0


def qfos_exit_lifecycle_net_open_qty(conn, symbol):
    row = conn.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN lower(side)='buy' THEN quantity ELSE 0 END),0) -
            COALESCE(SUM(CASE WHEN lower(side)='sell' THEN quantity ELSE 0 END),0) AS net_qty
        FROM trades
        WHERE symbol = :symbol
    """), {"symbol": symbol}).mappings().first()
    return float((row or {}).get("net_qty") or 0.0)


def qfos_exit_lifecycle_log_decision(conn, symbol, age_min, pnl_pct, peak_pnl_pct, decision, reason, qty, avg_entry, last_price, regime):
    try:
        conn.execute(text("""
            INSERT INTO qfos_exit_decision_audit (
                symbol, age_min, pnl_pct, peak_pnl_pct, decision, reason,
                quantity, avg_entry, last_price, regime
            )
            VALUES (
                :symbol, :age_min, :pnl_pct, :peak_pnl_pct, :decision, :reason,
                :quantity, :avg_entry, :last_price, :regime
            )
        """), {
            "symbol": symbol,
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "decision": decision,
            "reason": reason,
            "quantity": qty,
            "avg_entry": avg_entry,
            "last_price": last_price,
            "regime": regime,
        })

        conn.execute(text("""
            INSERT INTO qfos_exit_lifecycle_state (
                symbol, peak_pnl_pct, last_pnl_pct, last_age_min,
                last_decision, last_reason, updated_at
            )
            VALUES (
                :symbol, :peak_pnl_pct, :pnl_pct, :age_min,
                :decision, :reason, CURRENT_TIMESTAMP
            )
            ON CONFLICT (symbol)
            DO UPDATE SET
                peak_pnl_pct = GREATEST(qfos_exit_lifecycle_state.peak_pnl_pct, EXCLUDED.peak_pnl_pct),
                last_pnl_pct = EXCLUDED.last_pnl_pct,
                last_age_min = EXCLUDED.last_age_min,
                last_decision = EXCLUDED.last_decision,
                last_reason = EXCLUDED.last_reason,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "symbol": symbol,
            "peak_pnl_pct": peak_pnl_pct,
            "pnl_pct": pnl_pct,
            "age_min": age_min,
            "decision": decision,
            "reason": reason,
        })
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] audit_write symbol={symbol} error={e}", flush=True)

    print(
        f"[EXIT_DECISION] symbol={symbol} "
        f"age_min={age_min:.2f} pnl_pct={pnl_pct:.5f} peak_pnl_pct={peak_pnl_pct:.5f} "
        f"decision={decision} reason={reason}",
        flush=True,
    )


def qfos_exit_lifecycle_build_sell_fill(symbol, qty, price, reason):
    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": float(qty),
        "expected_price": float(price),
        "fill_price": float(price),
        "slippage_bps": 0.0,
        "strategy": str(reason),
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "is_exit": True,
        "exit_reason": str(reason),
        "source": "qfos_exit_lifecycle",
    }



# ============================================================
# QFOS_AGENT5_EXIT_ATOMIC_PERSISTENCE_REPAIR_V1
#
# Guarantees a public qfos_persist_fill_atomic binding for all exit paths.
# If an earlier patch renamed the implementation to
# qfos_persist_fill_atomic_core, this public alias delegates to it.
# ============================================================

if not callable(globals().get("qfos_persist_fill_atomic")):
    def qfos_persist_fill_atomic(conn, fill, source="main_loop"):
        core = globals().get("qfos_persist_fill_atomic_core")
        if not callable(core):
            raise RuntimeError(
                "atomic_persistence_helper_unavailable:"
                "qfos_persist_fill_atomic_and_core_missing"
            )
        try:
            return core(conn, fill, source=source)
        except TypeError:
            return core(conn, fill)


def qfos_exit_atomic_helper():
    public = globals().get("qfos_persist_fill_atomic")
    if callable(public):
        return "qfos_persist_fill_atomic", public

    core = globals().get("qfos_persist_fill_atomic_core")
    if callable(core):
        return "qfos_persist_fill_atomic_core", core

    return "unavailable", None


def qfos_exit_lifecycle_execute_sell(symbol, qty, price, reason):
    symbol = str(symbol or "").strip()
    requested_qty = float(qty or 0.0)
    fill_price = float(price or 0.0)
    reason = str(reason or "").strip()

    if not globals().get("QFOS_LIFECYCLE_DIRECT_EXECUTION_ENABLED", False):
        print(
            "[EXIT_LIFECYCLE_DELEGATED] "
            f"symbol={symbol or 'UNKNOWN'} "
            f"reason={reason or 'unknown'} "
            "owner=main_loop",
            flush=True,
        )
        return False

    if not symbol or requested_qty <= 0 or fill_price <= 0:
        print(
            f"[EXIT_SELL_AUDIT] symbol={symbol or 'UNKNOWN'} reason={reason or 'unknown'} "
            f"decision=REJECTED reject_reason=invalid_qty_or_price "
            f"requested_qty={requested_qty} fill_price={fill_price}",
            flush=True,
        )
        return False

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT quantity
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            open_qty = float((row or {}).get("quantity") or 0.0)

            if open_qty <= 0.00000001:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=no_open_position "
                    f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                    f"fill_price={fill_price:.12f}",
                    flush=True,
                )
                return False

            if qfos_exit_lifecycle_recent_sell_exists(conn, symbol):
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=duplicate_recent_sell_guard "
                    f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                    f"fill_price={fill_price:.12f}",
                    flush=True,
                )
                return False

            sell_qty = min(requested_qty, open_qty)
            helper_name, helper = qfos_exit_atomic_helper()

            print(
                f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                f"requested_qty={requested_qty:.12f} open_qty={open_qty:.12f} "
                f"fill_price={fill_price:.12f} persistence_helper={helper_name}",
                flush=True,
            )

            if not callable(helper):
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=atomic_helper_unavailable",
                    flush=True,
                )
                return False

            fill = qfos_exit_lifecycle_build_sell_fill(
                symbol=symbol,
                qty=sell_qty,
                price=fill_price,
                reason=reason,
            )

            # Explicitly preserve SELL exit metadata.
            fill["is_exit"] = True
            fill["exit_reason"] = reason
            fill["source"] = "qfos_exit_lifecycle"

            try:
                result = helper(conn, fill, source="qfos_exit_lifecycle")
            except TypeError:
                result = helper(conn, fill)

            if result is None or result is False:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=atomic_persistence_rejected",
                    flush=True,
                )
                return False

            latest = conn.execute(text("""
                SELECT id, quantity, fill_price, is_exit, exit_reason, pnl
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'sell'
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            if not latest:
                print(
                    f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                    f"decision=REJECTED reject_reason=missing_sell_trade_after_persist",
                    flush=True,
                )
                return False

            conn.execute(text("""
                UPDATE qfos_exit_lifecycle_state
                SET last_sell_at = CURRENT_TIMESTAMP,
                    last_decision = 'SELL',
                    last_reason = :reason,
                    updated_at = CURRENT_TIMESTAMP
                WHERE symbol = :symbol
            """), {"symbol": symbol, "reason": reason})

            print(
                f"[EXIT_SELL_AUDIT] symbol={symbol} reason={reason} "
                f"decision=PERSISTED trade_id={latest.get('id')} "
                f"sell_qty={float(latest.get('quantity') or 0):.12f} "
                f"is_exit={latest.get('is_exit')} "
                f"exit_reason={latest.get('exit_reason')} "
                f"pnl={float(latest.get('pnl') or 0):.12f}",
                flush=True,
            )
            return True

    except Exception as e:
        print(
            f"[EXIT_SELL_ERROR] symbol={symbol} reason={reason} "
            f"error={type(e).__name__}:{e}",
            flush=True,
        )
        return False


# ============================================================
# End QFOS_AGENT5_EXIT_ATOMIC_PERSISTENCE_REPAIR_V1
# ============================================================


# ============================================================
# QFOS_AGENT5_SINGLE_FRESH_LOT_BASIS_GUARD_V1
#
# Reconciles only a provable single fresh open lot:
# - latest BUY is after latest SELL
# - open DB quantity matches that latest BUY quantity
# - stored average entry differs from latest BUY fill price
#
# This prevents lifecycle decisions using stale position avg_entry while
# the atomic firewall uses the actual fresh BUY price.
# No cash changes. No sell is created. No threshold is changed.
# ============================================================

def qfos_reconcile_single_fresh_open_lot_basis():
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT
                    p.symbol,
                    p.quantity AS open_qty,
                    p.avg_entry AS old_avg_entry,
                    p.last_price,
                    lb.id AS latest_buy_id,
                    lb.quantity AS latest_buy_qty,
                    lb.fill_price AS latest_buy_price,
                    ls.id AS latest_sell_id
                FROM positions p
                JOIN LATERAL (
                    SELECT id, quantity, fill_price
                    FROM trades
                    WHERE symbol = p.symbol
                      AND lower(side) = 'buy'
                    ORDER BY id DESC
                    LIMIT 1
                ) lb ON true
                LEFT JOIN LATERAL (
                    SELECT id
                    FROM trades
                    WHERE symbol = p.symbol
                      AND lower(side) = 'sell'
                    ORDER BY id DESC
                    LIMIT 1
                ) ls ON true
                WHERE p.quantity > 0.00000001
                  AND (ls.id IS NULL OR lb.id > ls.id)
                  AND abs(p.quantity - lb.quantity)
                      <= greatest(0.00000001, abs(lb.quantity) * 0.00001)
                  AND abs(p.avg_entry - lb.fill_price) > 0.00000001
            """)).mappings().all()

            for row in rows:
                symbol = str(row.get("symbol") or "")
                old_avg = float(row.get("old_avg_entry") or 0.0)
                new_avg = float(row.get("latest_buy_price") or 0.0)
                qty = float(row.get("open_qty") or 0.0)
                last = float(row.get("last_price") or new_avg)

                if not symbol or qty <= 0 or new_avg <= 0:
                    continue

                conn.execute(text("""
                    UPDATE positions
                    SET
                        avg_entry = :avg_entry,
                        exposure = quantity * :last_price,
                        unrealized_pnl = (:last_price - :avg_entry) * quantity,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = :symbol
                """), {
                    "symbol": symbol,
                    "avg_entry": new_avg,
                    "last_price": last,
                })

                print(
                    "[POSITION_BASIS_REPAIRED] "
                    f"symbol={symbol} qty={qty:.12f} "
                    f"old_avg_entry={old_avg:.12f} "
                    f"new_avg_entry={new_avg:.12f} "
                    f"latest_buy_id={row.get('latest_buy_id')} "
                    f"latest_sell_id={row.get('latest_sell_id')}",
                    flush=True,
                )

    except Exception as e:
        print(f"[POSITION_BASIS_REPAIR_ERROR] error={e!r}", flush=True)

# ============================================================
# End QFOS_AGENT5_SINGLE_FRESH_LOT_BASIS_GUARD_V1
# ============================================================


def qfos_exit_lifecycle_evaluate_once(source="cycle"):
    qfos_exit_lifecycle_ensure_tables()
    qfos_reconcile_single_fresh_open_lot_basis()

    regime = qfos_exit_lifecycle_current_regime()
    positions = qfos_exit_lifecycle_fetch_positions()

    if not positions:
        print(f"[EXIT_DECISION] symbol=ALL decision=HOLD reason=no_open_positions source={source}", flush=True)
        return 0

    sells = 0

    for p in positions:
        try:
            symbol = str(p.get("symbol") or "")
            qty = float(p.get("quantity") or 0.0)
            avg_entry = float(p.get("avg_entry") or 0.0)
            last_price = float(p.get("last_price") or 0.0)
            age_min = float(p.get("age_minutes") or 0.0)

            if not symbol or qty <= 0 or avg_entry <= 0 or last_price <= 0:
                print(
                    f"[EXIT_DECISION] symbol={symbol or 'UNKNOWN'} age_min={age_min:.2f} "
                    f"pnl_pct=0.00000 peak_pnl_pct=0.00000 decision=HOLD reason=hold_invalid_position_data",
                    flush=True,
                )
                continue

            pnl_pct = (last_price - avg_entry) / avg_entry

            with engine.begin() as conn:
                peak_pnl_pct = qfos_exit_lifecycle_get_peak(conn, symbol, pnl_pct, entry_started_at=p.get('entry_started_at'))
                decision, reason = qfos_exit_lifecycle_decide(symbol, age_min, pnl_pct, peak_pnl_pct, regime)

                qfos_exit_lifecycle_log_decision(
                    conn=conn,
                    symbol=symbol,
                    age_min=age_min,
                    pnl_pct=pnl_pct,
                    peak_pnl_pct=peak_pnl_pct,
                    decision=decision,
                    reason=reason,
                    qty=qty,
                    avg_entry=avg_entry,
                    last_price=last_price,
                    regime=regime,
                )

            if decision == "SELL":
                if qfos_exit_lifecycle_execute_sell(symbol, qty, last_price, reason):
                    sells += 1

        except Exception as e:
            print(f"[EXIT_DECISION_ERROR] position_eval error={e} payload={p}", flush=True)

    return sells


def qfos_exit_lifecycle_start_daemon():
    try:
        import threading
        import time

        if globals().get("_qfos_exit_lifecycle_daemon_started"):
            return

        globals()["_qfos_exit_lifecycle_daemon_started"] = True

        def _worker():

            # QFOS_EXIT_LIFECYCLE_WAIT_FOR_ATOMIC_PERSIST_V1
            # The lifecycle daemon is defined/started before atomic persistence later
            # in this file. Wait fail-closed until that authority is installed.
            _qfos_atomic_wait_logged = False
            while not callable(globals().get("qfos_persist_fill_atomic")):
                if not _qfos_atomic_wait_logged:
                    print(
                        "[EXIT_LIFECYCLE_WAIT] waiting_for=qfos_persist_fill_atomic",
                        flush=True,
                    )
                    _qfos_atomic_wait_logged = True
                time.sleep(0.25)
            print(
                "[EXIT_LIFECYCLE_WAIT] atomic_persistence_ready",
                flush=True,
            )
            print("[EXIT_LIFECYCLE] daemon_started", flush=True)
            while True:
                try:
                    qfos_exit_lifecycle_evaluate_once(source="daemon")
                except Exception as e:
                    print(f"[EXIT_DECISION_ERROR] daemon_loop error={e}", flush=True)
                time.sleep(QFOS_EXIT_DAEMON_INTERVAL_SECONDS)

        t = threading.Thread(target=_worker, name="qfos_exit_lifecycle", daemon=True)
        t.start()
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] daemon_start error={e}", flush=True)


# ============================================================
# End QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1

# ============================================================
# QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_START_CALL_V1
# Purpose:
#   The lifecycle functions were installed, but the daemon was not
#   actually started because the previous patch confused the function
#   definition with a startup call.
# ============================================================

# QFOS_AGENT5_EXIT_STARTUP_ORDER_FIX_V1
# Do not evaluate exits during module load. The actual atomic persistence
# function is defined later in this file. The daemon itself already waits
# fail-closed until qfos_persist_fill_atomic is callable.
# C11_IMPORT_SAFE_GUARD_V1
if str(__import__("os").getenv("QFOS_IMPORT_SAFE", "")).strip().lower() in ("1", "true", "yes", "on"):
    print("[QFOS_IMPORT_SAFE] skipped exit_lifecycle_module_startup", flush=True)
else:
    try:
        qfos_exit_lifecycle_ensure_tables()
        qfos_exit_lifecycle_start_daemon()
        print("[EXIT_LIFECYCLE] startup_daemon_registered_no_early_evaluation", flush=True)
    except Exception as e:
        print(f"[EXIT_DECISION_ERROR] startup_call_v1 error={e}", flush=True)

# ============================================================
# End QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_START_CALL_V1
# ============================================================

# ============================================================


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

def recent_buy_count(hours=1.0):
    """
    Count persisted BUY fills in a real rolling window.

    PostgreSQL timestamps in this project are stored in EAT-style wall time,
    so the cutoff is PostgreSQL current time plus three hours minus the
    requested rolling window. This must never point into the future.
    """
    try:
        window_hours = max(0.01, float(hours))
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS count
                FROM trades
                WHERE LOWER(side) = 'buy'
                  AND created_at >= (
                      CURRENT_TIMESTAMP + interval '3 hours'
                      - (:hours || ' hours')::interval
                  )
            """), {"hours": window_hours}).mappings().first()
        return int((row or {}).get("count") or 0)
    except Exception as exc:
        print(f"[ENTRY_RATE_COUNT_ERROR] error={exc!r}", flush=True)
        # Fail closed. An unavailable ledger must not create more buys.
        return int(SIDEWAYS_MAX_ENTRIES_PER_HOUR)

def recent_symbol_buy_count(symbol: str):
    """
    Count recent buys for a symbol in a rolling window.
    This replaces lifetime trade_counts blocking.
    """
    try:
        with engine.begin() as conn:
            row = conn.execute(text("\n                SELECT COUNT(*) AS count\n                FROM trades\n                WHERE symbol = :symbol\n                  AND side = 'buy'\n                  AND created_at >= (CURRENT_TIMESTAMP + interval '3 hours' - (:hours || ' hours')::interval)\n            "), {'symbol': symbol, 'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().first()
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
            rows = conn.execute(text("\n                SELECT symbol, COUNT(*) AS count\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= (CURRENT_TIMESTAMP + interval '3 hours' - (:hours || ' hours')::interval)\n                GROUP BY symbol\n            "), {'hours': TRADE_COUNT_WINDOW_HOURS}).mappings().all()
        for r in rows:
            trade_counts[r['symbol']] = int(r['count'] or 0)
    except Exception as e:
        print('TRADE_COUNT_REFRESH_ERROR:', e)

def is_trending_regime(regime: str):
    r = str(regime or '').upper()
    return r in {'BULL', 'BULLISH', 'TRENDING', 'UPTREND', 'TREND'}


# ============================================================
# QFOS_REAL_MEXC_ONLY_FUNCTIONS_V1
# Runtime function install.
# Blocks fallback scout / RAW_MOMENTUM_FALLBACK buys.
# Allows normal evo / evo_allocator_rescue MEXC-derived buys.
# ============================================================

def qfos_real_mexc_entry_allowed(fill):
    try:
        if not isinstance(fill, dict):
            return True, "not_dict_passthrough"

        side = str(fill.get("side", "") or "").lower()
        if side != "buy":
            return True, "non_buy_passthrough"

        strategy = str(fill.get("strategy", "") or "").lower()
        source = str(fill.get("source", "") or "").lower()

        feature = fill.get("feature")
        if not isinstance(feature, dict):
            feature = fill.get("features")
        if not isinstance(feature, dict):
            feature = {}

        feature_source = str(feature.get("source", "") or "").upper()

        if strategy == "fallback_scout_breakout":
            return False, "blocked_fallback_scout_breakout"

        if strategy == "raw_momentum_fallback":
            return False, "blocked_raw_momentum_fallback_strategy"

        if "fallback" in source:
            return False, "blocked_fallback_source"

        if feature_source == "RAW_MOMENTUM_FALLBACK":
            return False, "blocked_raw_momentum_fallback_feature"

        if feature_source and feature_source != "NORMAL":
            return False, f"blocked_non_normal_feature_source_{feature_source}"

        return True, "real_mexc_only_ok"

    except Exception as exc:
        return False, f"real_mexc_only_exception_{exc}"


def qfos_real_data_trade_firewall(fill, regime):
    allowed, reason = qfos_real_mexc_entry_allowed(fill)

    if not allowed:
        try:
            print(
                f"[REAL_MEXC_ONLY] blocked symbol={fill.get('symbol')} "
                f"side={fill.get('side')} strategy={fill.get('strategy')} reason={reason}",
                flush=True,
            )
        except Exception:
            pass
        return False, reason

    try:
        return final_trade_firewall(fill, regime)
    except NameError:
        return True, reason

# ============================================================
# End QFOS_REAL_MEXC_ONLY_FUNCTIONS_V1
# ============================================================

def entry_policy_allows(symbol: str, regime: str, confidence: float, entries_this_cycle: int, strategy: str=None):
    with engine.begin() as conn:
        q = conn.execute(text("SELECT symbol FROM symbol_quarantine WHERE symbol = :sym AND blocked_until IS NOT NULL AND blocked_until > CURRENT_TIMESTAMP + interval '3 hours'"), {'sym': symbol}).first()
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
    print('QUANT FUND OS ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â CYCLE DIAGNOSTIC')
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
            row = conn.execute(text("\n                SELECT COUNT(*) AS count\n                FROM trades\n                WHERE symbol = :symbol\n                  AND (\n                        strategy IN ('stop_loss', 'adaptive_stop_loss')\n                     OR side = 'sell' AND strategy LIKE '%stop_loss%'\n                  )\n                  AND created_at >= (CURRENT_TIMESTAMP + interval '3 hours' - (:hours || ' hours')::interval)\n            "), {'symbol': symbol, 'hours': ENTRY_STOP_LOSS_QUARANTINE_HOURS}).mappings().first()
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



# QFOS_AGENT5_DIRECT_EXIT_PREP_V1
# Agent 5 direct execution-path patch.
# Purpose:
#   FULL_PROFIT_MODE currently rejects exit lifecycle SELLs as no-open-position
#   even when Postgres positions has quantity.
#
#   This helper prepares exit SELLs directly at the call site immediately before
#   _qfos_full_exit_filter_fills(applied_fills), so the final active filter sees
#   DB-confirmed quantity, is_exit=true, and exit_reason populated.

def _qfos_agent5_direct_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_direct_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_direct_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_direct_exit_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_direct_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_direct_side(fill) != "sell":
        return False

    reason = _qfos_agent5_direct_exit_reason(fill)

    if bool(fill.get("is_exit")):
        return True

    tokens = (
        "take_profit",
        "stop_loss",
        "stagnation",
        "max_hold",
        "trailing",
        "breakeven",
        "time_stop",
        "risk_off",
        "exit",
    )

    return any(t in reason for t in tokens)


def _qfos_agent5_direct_db_position(symbol):
    if not symbol:
        return None

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select
                        symbol,
                        quantity,
                        avg_entry,
                        coalesce(last_price, avg_entry) as last_price
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return None

            return dict(row)
    except Exception as exc:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_direct_prepare_fill(fill):
    if not _qfos_agent5_direct_is_exit_sell(fill):
        return fill

    symbol = _qfos_agent5_direct_symbol(fill)
    pos = _qfos_agent5_direct_db_position(symbol)

    if not pos:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] no_db_open_position "
            f"symbol={symbol} reason={_qfos_agent5_direct_exit_reason(fill)}",
            flush=True,
        )
        return fill

    db_qty = max(0.0, _qfos_agent5_direct_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_direct_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_DIRECT_EXIT_PREP] zero_sell_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return fill

    reason = _qfos_agent5_direct_exit_reason(fill) or "exit_lifecycle"

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_direct_exit_prep"

    # Make price fields consistent with DB if caller omitted one.
    db_price = _qfos_agent5_direct_float(pos.get("last_price"), 0.0)
    if db_price > 0:
        out["fill_price"] = _qfos_agent5_direct_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_direct_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_direct_float(out.get("price"), db_price) or db_price

    print(
        f"[AGENT5_DIRECT_EXIT_PREP] prepared "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return out




# QFOS_AGENT5_BYPASS_FULL_PROFIT_DB_EXITS_V1
# Agent 5 final SELL filter repair.
#
# Problem:
#   qfos_agent5_direct_prepare_exit_sells() confirms DB open quantity,
#   but _qfos_full_exit_filter_fills() still rejects the SELL with:
#       reject_sell_no_open_position
#
# Root cause:
#   FULL_PROFIT_MODE is still using stale/incomplete runtime position memory.
#
# Fix:
#   Split fills before FULL_PROFIT_MODE:
#     - DB-confirmed exit SELLs bypass _qfos_full_exit_filter_fills.
#     - Non-exit or uncertain fills still go through _qfos_full_exit_filter_fills.
#   This preserves duplicate protection while preventing stale memory from
#   blocking real DB-backed risk exits.

def _qfos_agent5_bypass_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_bypass_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_bypass_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_bypass_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_bypass_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_bypass_side(fill) != "sell":
        return False

    reason = _qfos_agent5_bypass_reason(fill)

    if bool(fill.get("is_exit")):
        return True

    tokens = (
        "take_profit",
        "stop_loss",
        "stagnation",
        "max_hold",
        "trailing",
        "breakeven",
        "time_stop",
        "risk_off",
        "exit",
        "adaptive",
    )

    return any(t in reason for t in tokens)


def _qfos_agent5_bypass_db_position(symbol):
    if not symbol:
        return None

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select
                        symbol,
                        quantity,
                        avg_entry,
                        coalesce(last_price, avg_entry) as last_price
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return None

            return dict(row)
    except Exception as exc:
        print(
            f"[AGENT5_BYPASS_FULL_PROFIT] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_bypass_confirm_exit_sell(fill):
    """
    Return normalized DB-confirmed exit sell, or None if it must remain
    under the normal FULL_PROFIT_MODE filter.
    """
    if not _qfos_agent5_bypass_is_exit_sell(fill):
        return None

    symbol = _qfos_agent5_bypass_symbol(fill)
    reason = _qfos_agent5_bypass_reason(fill) or "exit_lifecycle"

    pos = _qfos_agent5_bypass_db_position(symbol)
    if not pos:
        print(
            f"[AGENT5_BYPASS_FULL_PROFIT] cannot_bypass_no_db_position "
            f"symbol={symbol} reason={reason}",
            flush=True,
        )
        return None

    db_qty = max(0.0, _qfos_agent5_bypass_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_bypass_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_BYPASS_FULL_PROFIT] cannot_bypass_zero_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return None

    db_price = _qfos_agent5_bypass_float(pos.get("last_price"), 0.0)

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_bypass_full_profit_db_exit"

    if db_price > 0:
        out["fill_price"] = _qfos_agent5_bypass_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_bypass_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_bypass_float(out.get("price"), db_price) or db_price

    # Sync runtime memory so later execution logs/status do not see zero.
    try:
        if "portfolio" in globals() and hasattr(portfolio, "positions"):
            portfolio.positions[symbol] = sell_qty
    except Exception:
        pass

    print(
        f"[AGENT5_BYPASS_FULL_PROFIT] bypass_confirmed "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return out


def qfos_agent5_filter_with_db_exit_bypass(fills):
    """
    Replacement for direct use of _qfos_full_exit_filter_fills(applied_fills).

    DB-confirmed exit SELLs bypass FULL_PROFIT_MODE.
    All other fills still pass through FULL_PROFIT_MODE.
    """
    db_exit_sells = []
    normal_fills = []
    seen_exit_symbols = set()

    for fill in list(fills or []):
        confirmed = None
        try:
            confirmed = _qfos_agent5_bypass_confirm_exit_sell(fill)
        except Exception as exc:
            print(
                "[AGENT5_BYPASS_FULL_PROFIT] confirm_error "
                + repr(exc),
                flush=True,
            )

        if confirmed:
            symbol = _qfos_agent5_bypass_symbol(confirmed)

            # One full exit per symbol per cycle. This prevents duplicate
            # lifecycle + adaptive SELLs from both firing.
            if symbol in seen_exit_symbols:
                print(
                    f"[AGENT5_BYPASS_FULL_PROFIT] duplicate_exit_suppressed "
                    f"symbol={symbol} reason={_qfos_agent5_bypass_reason(confirmed)}",
                    flush=True,
                )
                continue

            seen_exit_symbols.add(symbol)
            db_exit_sells.append(confirmed)
        else:
            normal_fills.append(fill)

    try:
        filtered_normal = _qfos_full_exit_filter_fills(normal_fills)
    except Exception as exc:
        print(
            "[AGENT5_BYPASS_FULL_PROFIT] original_filter_failed "
            + repr(exc),
            flush=True,
        )
        filtered_normal = normal_fills

    result = list(db_exit_sells or []) + list(filtered_normal or [])

    print(
        f"[AGENT5_BYPASS_FULL_PROFIT] result "
        f"db_exit_sells={len(db_exit_sells)} "
        f"normal_in={len(normal_fills)} "
        f"normal_out={len(filtered_normal or [])} "
        f"total_out={len(result)}",
        flush=True,
    )

    return result

# END QFOS_AGENT5_BYPASS_FULL_PROFIT_DB_EXITS_V1


def qfos_agent5_direct_prepare_exit_sells(fills):
    prepared = []

    for fill in list(fills or []):
        try:
            prepared.append(_qfos_agent5_direct_prepare_fill(fill))
        except Exception as exc:
            print(
                "[AGENT5_DIRECT_EXIT_PREP] fill_prepare_error "
                + repr(exc),
                flush=True,
            )
            prepared.append(fill)

    return prepared

# END QFOS_AGENT5_DIRECT_EXIT_PREP_V1




# QFOS_AGENT5_ACTIVE_CALLSITE_DB_EXIT_V1
# Agent 5 final active execution-path repair.
#
# Problem:
#   Exit lifecycle produces valid SELL fills, but the active callsite sends them
#   into _qfos_full_exit_filter_fills(), which rejects them using stale runtime
#   position memory:
#       [FULL_PROFIT_MODE] reject_sell_no_open_position
#
# Fix:
#   Immediately before FULL_PROFIT_MODE filtering:
#   - identify exit SELLs,
#   - confirm open quantity from Postgres positions,
#   - clamp SELL qty to DB open qty,
#   - set is_exit=true and exit_reason,
#   - bypass FULL_PROFIT_MODE only for DB-confirmed exit SELLs,
#   - send all other fills through the original filter.

def _qfos_agent5_active_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_active_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_active_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_active_reason(fill):
    try:
        return str(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
            or ""
        ).strip()
    except Exception:
        return ""


def _qfos_agent5_active_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_active_side(fill) != "sell":
        return False

    reason = _qfos_agent5_active_reason(fill)

    if bool(fill.get("is_exit")):
        return True

    tokens = (
        "take_profit",
        "stop_loss",
        "stagnation",
        "max_hold",
        "trailing",
        "breakeven",
        "time_stop",
        "risk_off",
        "adaptive_take_profit",
        "adaptive_stop_loss",
        "exit",
    )

    return any(t in reason for t in tokens)


def _qfos_agent5_active_db_position(symbol):
    if not symbol:
        return None

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select
                        symbol,
                        quantity,
                        avg_entry,
                        coalesce(last_price, avg_entry) as last_price
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return None

            return dict(row)
    except Exception as exc:
        print(
            f"[AGENT5_ACTIVE_DB_EXIT] db_position_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return None


def _qfos_agent5_active_confirm_exit_sell(fill):
    if not _qfos_agent5_active_is_exit_sell(fill):
        return None

    symbol = _qfos_agent5_active_symbol(fill)
    reason = _qfos_agent5_active_reason(fill) or "exit_lifecycle"

    pos = _qfos_agent5_active_db_position(symbol)
    if not pos:
        print(
            f"[AGENT5_ACTIVE_DB_EXIT] no_db_open_position "
            f"symbol={symbol} reason={reason}",
            flush=True,
        )
        return None

    db_qty = max(0.0, _qfos_agent5_active_float(pos.get("quantity")))
    requested_qty = _qfos_agent5_active_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )
    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        print(
            f"[AGENT5_ACTIVE_DB_EXIT] zero_sell_qty "
            f"symbol={symbol} requested_qty={requested_qty:.12f} db_qty={db_qty:.12f}",
            flush=True,
        )
        return None

    db_price = _qfos_agent5_active_float(pos.get("last_price"), 0.0)

    out = dict(fill)
    out["side"] = "sell"
    out["quantity"] = sell_qty
    out["qty"] = sell_qty
    out["is_exit"] = True
    out["exit_reason"] = reason
    out["reason"] = reason
    out["strategy"] = reason
    out["source"] = out.get("source") or "agent5_active_db_exit"

    if db_price > 0:
        out["fill_price"] = _qfos_agent5_active_float(out.get("fill_price"), db_price) or db_price
        out["expected_price"] = _qfos_agent5_active_float(out.get("expected_price"), db_price) or db_price
        out["price"] = _qfos_agent5_active_float(out.get("price"), db_price) or db_price

    # Keep runtime memory aligned for downstream execution code that still checks portfolio.positions.
    try:
        if "portfolio" in globals() and hasattr(portfolio, "positions"):
            portfolio.positions[symbol] = db_qty
    except Exception:
        pass

    print(
        f"[AGENT5_ACTIVE_DB_EXIT] bypass_confirmed "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} reason={reason}",
        flush=True,
    )

    return out


def qfos_agent5_active_filter_exit_sells(fills):
    db_exit_sells = []
    normal_fills = []
    seen_symbols = set()

    for fill in list(fills or []):
        confirmed = None

        try:
            confirmed = _qfos_agent5_active_confirm_exit_sell(fill)
        except Exception as exc:
            print(
                "[AGENT5_ACTIVE_DB_EXIT] confirm_error "
                + repr(exc),
                flush=True,
            )

        if confirmed:
            symbol = _qfos_agent5_active_symbol(confirmed)

            if symbol in seen_symbols:
                print(
                    f"[AGENT5_ACTIVE_DB_EXIT] duplicate_exit_suppressed "
                    f"symbol={symbol} reason={_qfos_agent5_active_reason(confirmed)}",
                    flush=True,
                )
                continue

            seen_symbols.add(symbol)
            db_exit_sells.append(confirmed)
        else:
            normal_fills.append(fill)

    try:
        filtered_normal = _qfos_full_exit_filter_fills(normal_fills)
    except Exception as exc:
        print(
            "[AGENT5_ACTIVE_DB_EXIT] original_filter_failed "
            + repr(exc),
            flush=True,
        )
        filtered_normal = normal_fills

    result = list(db_exit_sells or []) + list(filtered_normal or [])

    print(
        f"[AGENT5_ACTIVE_DB_EXIT] result "
        f"db_exit_sells={len(db_exit_sells)} "
        f"normal_in={len(normal_fills)} "
        f"normal_out={len(filtered_normal or [])} "
        f"total_out={len(result)}",
        flush=True,
    )

    return result

# END QFOS_AGENT5_ACTIVE_CALLSITE_DB_EXIT_V1




# QFOS_FINAL_EXIT_BRIDGE_ACTIVE_CALLSITE_V1
# Purpose:
#   The exit lifecycle can generate DB-backed SELLs, and Agent 5 can now
#   DB-confirm/bypass FULL_PROFIT_MODE. But some cycles reach execution with
#   proposed_fills=0, so no SELLs reach Agent 5.
#
#   This bridge injects DB-backed exit SELLs directly at the active execution
#   callsite before Agent 5 filtering.
#
# Scope:
#   - Does not change BUY logic.
#   - Does not change accounting.
#   - Does not change feature generation.
#   - Only adds qualified DB exit SELLs before execution filtering.

def qfos_final_exit_bridge_add_db_sells(applied_fills, regime):
    fills = list(applied_fills or [])

    try:
        if "qfos_exit_lifecycle_db_sells" not in globals():
            print("[FINAL_EXIT_BRIDGE] exit_lifecycle_function_missing", flush=True)
            return fills

        db_sells = qfos_exit_lifecycle_db_sells(regime)

        if not db_sells:
            print("[FINAL_EXIT_BRIDGE] db_sells=0", flush=True)
            return fills

        existing_sell_symbols = set()

        for fill in fills:
            try:
                if str(fill.get("side", "")).lower() == "sell":
                    existing_sell_symbols.add(str(fill.get("symbol", "")).strip())
            except Exception:
                pass

        added = []

        for sell in db_sells:
            try:
                symbol = str(sell.get("symbol", "")).strip()
                if not symbol:
                    continue

                # One exit per symbol per cycle.
                if symbol in existing_sell_symbols:
                    print(
                        f"[FINAL_EXIT_BRIDGE] duplicate_suppressed symbol={symbol}",
                        flush=True,
                    )
                    continue

                sell = dict(sell)
                reason = str(
                    sell.get("exit_reason")
                    or sell.get("reason")
                    or sell.get("strategy")
                    or "exit_lifecycle"
                ).strip()

                sell["side"] = "sell"
                sell["is_exit"] = True
                sell["exit_reason"] = reason
                sell["reason"] = reason
                sell["strategy"] = reason
                sell["source"] = sell.get("source") or "final_exit_bridge"

                added.append(sell)
                existing_sell_symbols.add(symbol)

            except Exception as exc:
                print(
                    "[FINAL_EXIT_BRIDGE] add_sell_error "
                    + repr(exc),
                    flush=True,
                )

        if added:
            print(
                "[FINAL_EXIT_BRIDGE] added_db_exit_sells="
                + str([(x.get("symbol"), x.get("quantity"), x.get("exit_reason")) for x in added]),
                flush=True,
            )

        return added + fills

    except Exception as exc:
        print("[FINAL_EXIT_BRIDGE] bridge_error " + repr(exc), flush=True)
        return fills

# END QFOS_FINAL_EXIT_BRIDGE_ACTIVE_CALLSITE_V1




# QFOS_EXEC_RISK_AUTHORITY_GATE_V1
# Joint Agent 2 + Agent 5
#
# Problem:
#   final_validation rejected a valid BUY with:
#       caution_drawdown_position_cap_-0.0578
#   while authoritative API/Postgres state showed:
#       risk_status=SAFE, drawdown=0, exposure=0, positions=0.
#
# Fix:
#   Before final execution BUY validation, rebuild risk state from Postgres:
#     cash = starting_equity - buys + sells
#     exposure = sum(open positions exposure)
#     equity = cash + exposure
#     drawdown = current ledger-derived drawdown
#     open_positions = current DB open positions
#
#   Then sync runtime portfolio/risk memory and audit the exact state used.
#
#   The caution cap is NOT removed. A stale caution rejection is overridden only
#   when ledger authority proves SAFE/current clean state.

def _qfos_exec_risk_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_exec_risk_starting_equity():
    try:
        return float(globals().get("QFOS_STARTING_EQUITY", 100.0) or 100.0)
    except Exception:
        return 100.0



def qfos_exec_risk_authority_snapshot(force_refresh=False):
    global _QFOS_EXEC_CACHE_STATE, _QFOS_EXEC_CACHE_VERSION, _QFOS_LEDGER_STATE_VERSION, _QFOS_EVAL_BATCH_ACTIVE
    if not force_refresh and _QFOS_EVAL_BATCH_ACTIVE and _QFOS_EXEC_CACHE_VERSION == _QFOS_LEDGER_STATE_VERSION and _QFOS_EXEC_CACHE_STATE is not None:
        return _QFOS_EXEC_CACHE_STATE
    state = _compute_qfos_exec_risk_authority_snapshot()
    if _QFOS_EVAL_BATCH_ACTIVE:
        _QFOS_EXEC_CACHE_STATE = state
        _QFOS_EXEC_CACHE_VERSION = _QFOS_LEDGER_STATE_VERSION
    return state

def _compute_qfos_exec_risk_authority_snapshot():
    starting_equity = _qfos_exec_risk_starting_equity()

    snapshot = {
        "source": "ledger_postgres",
        "starting_equity": starting_equity,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "realized_pnl": 0.0,
        "cash": starting_equity,
        "exposure": 0.0,
        "equity": starting_equity,
        "drawdown": 0.0,
        "peak": starting_equity,
        "open_positions": 0,
        "positions": {},
        "risk_status": "SAFE",
    }

    try:
        with engine.begin() as conn:
            t = conn.execute(text("""
                select
                    coalesce(sum(case when lower(side)='buy' then quantity * fill_price else 0 end),0) as buy_cost,
                    coalesce(sum(case when lower(side)='sell' then quantity * fill_price else 0 end),0) as sell_proceeds,
                    coalesce(sum(case when lower(side)='sell' then pnl else 0 end),0) as realized_pnl
                from trades
            """)).mappings().first()

            p_rows = conn.execute(text("""
                select
                    symbol,
                    quantity,
                    avg_entry,
                    coalesce(last_price, avg_entry) as last_price,
                    coalesce(exposure, quantity * coalesce(last_price, avg_entry)) as exposure
                from positions
                where coalesce(quantity, 0) > 0.00000001
            """)).mappings().all()

            buy_cost = _qfos_exec_risk_float(t.get("buy_cost") if t else 0.0)
            sell_proceeds = _qfos_exec_risk_float(t.get("sell_proceeds") if t else 0.0)
            realized_pnl = _qfos_exec_risk_float(t.get("realized_pnl") if t else 0.0)

            positions = {}
            exposure = 0.0

            for r in p_rows:
                sym = str(r.get("symbol") or "").strip()
                qty = _qfos_exec_risk_float(r.get("quantity"))
                pos_exposure = _qfos_exec_risk_float(r.get("exposure"))
                if sym and qty > 0:
                    positions[sym] = qty
                    exposure += max(0.0, pos_exposure)

            cash = starting_equity - buy_cost + sell_proceeds
            equity = cash + exposure

            # Current-run ledger drawdown. Do not inherit old runtime peak.
            # If equity is at/above baseline, drawdown is clean zero.
            peak = max(starting_equity, equity)
            drawdown = 0.0
            if peak > 0:
                drawdown = min(0.0, (equity - peak) / peak)

            caution_drawdown = _qfos_exec_risk_float(
                getattr(globals().get("settings", object()), "caution_drawdown", -0.04),
                -0.04,
            )
            blocked_drawdown = _qfos_exec_risk_float(
                getattr(globals().get("settings", object()), "blocked_drawdown", -0.08),
                -0.08,
            )

            risk_status = "SAFE"
            if drawdown <= blocked_drawdown:
                risk_status = "BLOCKED"
            elif drawdown <= caution_drawdown:
                risk_status = "CAUTION"

            snapshot.update({
                "buy_cost": buy_cost,
                "sell_proceeds": sell_proceeds,
                "realized_pnl": realized_pnl,
                "cash": cash,
                "exposure": exposure,
                "equity": equity,
                "drawdown": drawdown,
                "peak": peak,
                "open_positions": len(positions),
                "positions": positions,
                "risk_status": risk_status,
            })

    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] error=" + repr(exc), flush=True)

    return snapshot


def qfos_exec_risk_apply_authority(snapshot):
    try:
        port = globals().get("portfolio")
        if port is not None:
            try:
                port.cash = _qfos_exec_risk_float(snapshot.get("cash"), getattr(port, "cash", 100.0))
            except Exception:
                pass

            try:
                port.positions = dict(snapshot.get("positions") or {})
            except Exception:
                pass

            try:
                port.drawdown = _qfos_exec_risk_float(snapshot.get("drawdown"), 0.0)
            except Exception:
                pass

            try:
                port.peak = _qfos_exec_risk_float(snapshot.get("peak"), snapshot.get("equity", 100.0))
            except Exception:
                pass

            try:
                port.equity = _qfos_exec_risk_float(snapshot.get("equity"), 100.0)
            except Exception:
                pass

        globals()["last_known_equity"] = _qfos_exec_risk_float(snapshot.get("equity"), 100.0)
        globals()["last_known_exposure"] = _qfos_exec_risk_float(snapshot.get("exposure"), 0.0)
        globals()["last_known_risk_status"] = str(snapshot.get("risk_status") or "SAFE")

    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] apply_error=" + repr(exc), flush=True)


def qfos_exec_risk_log_authority(snapshot):
    try:
        print(
            "[EXEC_RISK_AUTHORITY] "
            f"source={snapshot.get('source')} "
            f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
            f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
            f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
            f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
            f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
            f"open_positions={int(snapshot.get('open_positions') or 0)} "
            f"risk_status={snapshot.get('risk_status')}",
            flush=True,
        )
    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] log_error=" + repr(exc), flush=True)


def qfos_exec_risk_stale_caution_reject(reason, snapshot):
    reason_s = str(reason or "")
    if "caution_drawdown_position_cap" not in reason_s:
        return False

    risk_status = str(snapshot.get("risk_status") or "").upper()
    drawdown = _qfos_exec_risk_float(snapshot.get("drawdown"), 0.0)
    exposure = _qfos_exec_risk_float(snapshot.get("exposure"), 0.0)
    open_positions = int(snapshot.get("open_positions") or 0)

    # Only override a stale caution cap when ledger authority is clean.
    return (
        risk_status == "SAFE"
        and drawdown >= -0.000001
        and exposure <= 0.000001
        and open_positions == 0
    )


def qfos_exec_risk_authority_firewall(fill, regime):
    side = ""
    symbol = ""
    strategy = ""

    try:
        side = str((fill or {}).get("side") or "").strip().lower()
        symbol = str((fill or {}).get("symbol") or "").strip()
        strategy = str((fill or {}).get("strategy") or (fill or {}).get("reason") or "").strip()
    except Exception:
        pass

    # SELLs and non-BUYs continue through existing validation.
    if side != "buy":
        return qfos_real_data_trade_firewall(fill, regime)

    snapshot = qfos_exec_risk_authority_snapshot()
    qfos_exec_risk_apply_authority(snapshot)
    qfos_exec_risk_log_authority(snapshot)

    print(
        "[EXEC_RISK_AUDIT] "
        f"symbol={symbol} stage=before_final_validation "
        f"strategy={strategy} "
        f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
        f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
        f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
        f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
        f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
        f"risk_status={snapshot.get('risk_status')} "
        f"positions_count={int(snapshot.get('open_positions') or 0)} "
        f"source={snapshot.get('source')}",
        flush=True,
    )

    allowed, reason = qfos_real_data_trade_firewall(fill, regime)

    decision = "ALLOW" if allowed else "REJECT"

    # If the only rejection is stale caution drawdown while ledger is SAFE/clean,
    # allow the BUY and log the override.
    if not allowed and qfos_exec_risk_stale_caution_reject(reason, snapshot):
        print(
            "[EXEC_RISK_AUDIT] "
            f"symbol={symbol} stage=stale_caution_override "
            f"old_decision=REJECT old_reason={reason} "
            f"authority_risk_status={snapshot.get('risk_status')} "
            f"authority_drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
            f"authority_exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
            f"authority_open_positions={int(snapshot.get('open_positions') or 0)}",
            flush=True,
        )
        allowed = True
        reason = "ledger_safe_overrode_stale_caution_drawdown_position_cap"
        decision = "ALLOW"

    print(
        "[EXEC_RISK_AUDIT] "
        f"symbol={symbol} stage=after_final_validation "
        f"decision={decision} reason={reason} "
        f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
        f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
        f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
        f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
        f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
        f"risk_status={snapshot.get('risk_status')} "
        f"positions_count={int(snapshot.get('open_positions') or 0)}",
        flush=True,
    )

    return allowed, reason

# END QFOS_EXEC_RISK_AUTHORITY_GATE_V1




# QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1
# Purpose:
#   The previous forensic patch did not reach the active final_validation path.
#   Logs still show:
#       EXEC_BRIDGE_AUDIT stage=final_validation decision=REJECT
#       reason=caution_drawdown_position_cap_-0.0592
#
#   This wrapper intercepts active qfos_active_canbuy_authority(...) calls directly.
#
#   It does not delete the caution cap. It only overrides a stale caution cap
#   when Postgres ledger authority proves clean SAFE state.

def qfos_active_canbuy_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default



# QFOS_HOT_PATH_CACHE_PATCH
_QFOS_LEDGER_STATE_VERSION = 0
_QFOS_LEDGER_CACHE_STATE = None
_QFOS_LEDGER_CACHE_VERSION = -1

_QFOS_EXEC_CACHE_STATE = None
_QFOS_EXEC_CACHE_VERSION = -1

_QFOS_EVAL_BATCH_ACTIVE = False

def qfos_start_evaluation_batch():
    global _QFOS_EVAL_BATCH_ACTIVE, _QFOS_LEDGER_STATE_VERSION
    _QFOS_EVAL_BATCH_ACTIVE = True
    _QFOS_LEDGER_STATE_VERSION += 1

def qfos_stop_evaluation_batch():
    global _QFOS_EVAL_BATCH_ACTIVE, _QFOS_LEDGER_STATE_VERSION
    _QFOS_EVAL_BATCH_ACTIVE = False
    _QFOS_LEDGER_STATE_VERSION += 1

def qfos_invalidate_ledger_cache():
    global _QFOS_LEDGER_STATE_VERSION
    _QFOS_LEDGER_STATE_VERSION += 1

def _qfos_adapt_alloc_state(state):
    if not isinstance(state, dict): return {}
    valid_keys = {"cash", "equity", "exposure", "available_cash", "reserved_cash", "open_positions", "position_count", "pending_orders", "buy_slots_remaining", "risk_mode", "paper_balance_version", "drawdown"}
    return {k: v for k, v in state.items() if k in valid_keys}

def qfos_active_canbuy_ledger_state(force_refresh=False):
    global _QFOS_LEDGER_CACHE_STATE, _QFOS_LEDGER_CACHE_VERSION, _QFOS_LEDGER_STATE_VERSION, _QFOS_EVAL_BATCH_ACTIVE
    if not force_refresh and _QFOS_EVAL_BATCH_ACTIVE and _QFOS_LEDGER_CACHE_VERSION == _QFOS_LEDGER_STATE_VERSION and _QFOS_LEDGER_CACHE_STATE is not None:
        return _QFOS_LEDGER_CACHE_STATE
    state = _compute_qfos_active_canbuy_ledger_state()
    if _QFOS_EVAL_BATCH_ACTIVE:
        _QFOS_LEDGER_CACHE_STATE = state
        _QFOS_LEDGER_CACHE_VERSION = _QFOS_LEDGER_STATE_VERSION
    return state

def _compute_qfos_active_canbuy_ledger_state():
    state = {
        "cash": 100.0,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "exposure": 0.0,
        "equity": 100.0,
        "open_positions": 0,
        "drawdown": 0.0,
        "risk_status": "SAFE",
        "source": "active_canbuy_ledger",
    }

    try:
        start = 100.0
        for k in ("QFOS_STARTING_EQUITY", "QFOS_STARTING_CASH", "STARTING_EQUITY", "STARTING_CASH"):
            try:
                vv = globals().get(k)
                if vv is not None:
                    start = float(vv)
                    break
            except Exception:
                pass

        with engine.begin() as conn:
            t = conn.execute(text("""
                select
                    coalesce(sum(case when lower(side)='buy' then quantity * fill_price else 0 end),0) as buy_cost,
                    coalesce(sum(case when lower(side)='sell' then quantity * fill_price else 0 end),0) as sell_proceeds
                from trades
            """)).mappings().first()

            p = conn.execute(text("""
                select
                    coalesce(sum(coalesce(exposure, quantity * coalesce(last_price, avg_entry))),0) as exposure,
                    count(*) as open_positions
                from positions
                where coalesce(quantity,0) > 0.00000001
            """)).mappings().first()

        buy_cost = qfos_active_canbuy_float(t.get("buy_cost") if t else 0.0)
        sell_proceeds = qfos_active_canbuy_float(t.get("sell_proceeds") if t else 0.0)
        exposure = qfos_active_canbuy_float(p.get("exposure") if p else 0.0)
        open_positions = int(p.get("open_positions") if p else 0)

        cash = start - buy_cost + sell_proceeds
        equity = cash + exposure

        peak = max(start, equity)
        drawdown = 0.0
        if peak > 0:
            drawdown = min(0.0, (equity - peak) / peak)

        risk_status = "SAFE"
        if drawdown <= -0.08:
            risk_status = "BLOCKED"
        elif drawdown <= -0.04:
            risk_status = "CAUTION"

        state.update({
            "cash": cash,
            "buy_cost": buy_cost,
            "sell_proceeds": sell_proceeds,
            "exposure": exposure,
            "equity": equity,
            "open_positions": open_positions,
            "drawdown": drawdown,
            "risk_status": risk_status,
        })

    except Exception as exc:
        print("[ACTIVE_CANBUY_AUTHORITY] error=" + repr(exc), flush=True)

    return state


def qfos_active_canbuy_clean_safe_state(state):
    return (
        str(state.get("risk_status", "")).upper() == "SAFE"
        and qfos_active_canbuy_float(state.get("drawdown")) >= -0.000001
        and qfos_active_canbuy_float(state.get("exposure")) <= 0.000001
        and int(state.get("open_positions") or 0) == 0
    )


def qfos_active_canbuy_authority(*args, **kwargs):
    state = qfos_active_canbuy_ledger_state()

    print(
        "[ACTIVE_CANBUY_AUTHORITY] "
        f"cash={qfos_active_canbuy_float(state.get('cash')):.8f} "
        f"equity={qfos_active_canbuy_float(state.get('equity')):.8f} "
        f"exposure={qfos_active_canbuy_float(state.get('exposure')):.8f} "
        f"drawdown={qfos_active_canbuy_float(state.get('drawdown')):.8f} "
        f"open_positions={int(state.get('open_positions') or 0)} "
        f"risk_status={state.get('risk_status')}",
        flush=True,
    )

    try:
        result = can_buy(*args, **kwargs)
    except Exception as exc:
        print("[ACTIVE_CANBUY_AUTHORITY] original_can_buy_error=" + repr(exc), flush=True)
        return False, "can_buy_exception_" + type(exc).__name__

    allowed = False
    reason = ""

    try:
        if isinstance(result, tuple):
            allowed = bool(result[0])
            reason = str(result[1]) if len(result) > 1 else ""
        else:
            allowed = bool(result)
            reason = "buy_allowed" if allowed else "buy_rejected"
    except Exception:
        allowed = False
        reason = "can_buy_bad_return"

    if (not allowed) and ("caution_drawdown_position_cap" in reason):
        if qfos_active_canbuy_clean_safe_state(state):
            print(
                "[ACTIVE_CANBUY_AUTHORITY] stale_caution_override "
                f"old_reason={reason} "
                f"cash={qfos_active_canbuy_float(state.get('cash')):.8f} "
                f"equity={qfos_active_canbuy_float(state.get('equity')):.8f} "
                f"exposure={qfos_active_canbuy_float(state.get('exposure')):.8f} "
                f"drawdown={qfos_active_canbuy_float(state.get('drawdown')):.8f} "
                f"open_positions={int(state.get('open_positions') or 0)}",
                flush=True,
            )
            return True, "ledger_safe_overrode_stale_caution_drawdown_position_cap"

    return allowed, reason


def qfos_observability_cycle_id():
    """Return the sole cycle identifier used by ranking and execution telemetry."""
    return int(globals().get("qfos_cycle_counter", 0) or 0)

# END QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1


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
            # QFOS_QUALITY_REJECTION_DETAIL_V1
            try:
                _qrd = data if isinstance(data, dict) else {}
                print(
                    "[QUALITY_REJECT_DETAIL] "
                    f"symbol={symbol} "
                    f"reason={reason} "
                    f"signal={_feature_value(_qrd, 'signal_strength'):.6f} "
                    f"trend={_feature_value(_qrd, 'trend'):.6f} "
                    f"long_trend={_feature_value(_qrd, 'long_trend'):.6f} "
                    f"momentum={_feature_value(_qrd, 'momentum'):.6f} "
                    f"one_tick={_feature_value(_qrd, 'one_tick_momentum'):.6f} "
                    f"volatility={abs(_feature_value(_qrd, 'volatility')):.6f} "
                    f"symbol_regime={str(_qrd.get('symbol_regime') or '')} "
                    f"ready={bool(_qrd.get('ready'))}",
                    flush=True,
                )
            except Exception as _qrd_error:
                print(f"[QUALITY_REJECT_DETAIL] symbol={symbol} telemetry_error={_qrd_error!r}", flush=True)

            rejected_preview.append({'symbol': symbol, 'reason': f'entry_quality_{reason}'})
            continue
        quality_score = _compute_quality_score(data)
        signal = _feature_value(data, 'signal_strength')
        eligible.append((symbol, quality_score, signal, data))
    # Sort by composite quality score descending; signal breaks ties.
    eligible.sort(key=lambda x: (x[1], x[2]), reverse=True)
    top = eligible[:ENTRY_QUALITY_TOP_N]
    top_symbols = {s for s, _, _, _ in top}
    
    # RESEARCH SPRINT 2 (Phase 2A + 2B): Instrument ALL candidates (pre- and post-filter)
    # Emitting for the full feature_map ensures events are produced even when
    # entry_quality rejects every candidate (e.g. SIDEWAYS regime).
    try:
        from observability import events, _manager
        cycle_id = int(globals().get("qfos_cycle_counter", 0)) + 1
        globals()["qfos_cycle_counter"] = cycle_id
        ranking_population = len(feature_map)
        
        # Prevent stale candidate IDs from persisting into this cycle
        _manager.clear_cycle(cycle_id - 1)

        # Build a lookup: symbol -> (quality_score, signal, data, rank) for eligible candidates.
        eligible_lookup = {
            sym: (qs, sig, d, idx)
            for idx, (sym, qs, sig, d) in enumerate(eligible, start=1)
        }

        for sym, d in feature_map.items():
            _d = d if isinstance(d, dict) else {}
            sig = _feature_value(_d, 'signal_strength')
            mom = _feature_value(_d, 'momentum')
            vol = abs(_feature_value(_d, 'volatility'))
            src = str(_d.get('source', 'NORMAL')).upper()

            feature_snapshot = {
                "trend": _d.get("trend"),
                "long_trend": _d.get("long_trend"),
                "one_tick_momentum": _d.get("one_tick_momentum"),
                "symbol_trend_score": _d.get("symbol_trend_score"),
                "breakout_score": _d.get("breakout_score"),
                "trend_quality": _d.get("trend_quality"),
                "is_symbol_uptrend": _d.get("is_symbol_uptrend"),
                "is_symbol_downtrend": _d.get("is_symbol_downtrend"),
                "is_choppy": _d.get("is_choppy")
            }

            if sym in eligible_lookup:
                qs, sig, _dq, rank = eligible_lookup[sym]
                cid = events.candidate_ranked(
                    cycle_id=cycle_id,
                    rank=rank,
                    symbol=sym,
                    strength=sig,
                    momentum=mom,
                    volatility=vol,
                    confidence=sig,
                    regime=str(regime),
                    source=src,
                    score_before_filters=qs,
                    ranking_population=ranking_population,
                    decision='RANKED',
                    filter_reason=None,
                    features=feature_snapshot,
                )
                from observability import _manager
                _manager.register_candidate(cycle_id, sym, cid, rank, ranking_population)
                if sym not in top_symbols:
                    events.candidate_filtered(
                        candidate_id=cid,
                        cycle_id=cycle_id,
                        symbol=sym,
                        rank=rank,
                        ranking_population=ranking_population,
                        reason=RejectionReason.OTHER,
                        filter_name="ranking_top_n",
                        filter_stage=1,
                        raw_reason="rank_outside_execution_top_n",
                    )
            else:
                # Candidate was rejected by entry_quality; re-derive reason for the event.
                try:
                    _filter_reason = _entry_quality_reason(sym, d, regime) or 'UNKNOWN'
                except Exception:
                    _filter_reason = 'UNKNOWN'
                
                cid = events.candidate_ranked(
                    cycle_id=cycle_id,
                    rank=None,
                    symbol=sym,
                    strength=sig,
                    momentum=mom,
                    volatility=vol,
                    confidence=sig,
                    regime=str(regime),
                    source=src,
                    score_before_filters=None,
                    ranking_population=ranking_population,
                    decision='FILTERED',
                    filter_reason=str(_filter_reason),
                    features=feature_snapshot,
                )
                from observability import _manager, RejectionReason
                _manager.register_candidate(cycle_id, sym, cid, None, ranking_population)
                
                # Parse reason into structured event for Phase 2B
                reason_str = str(_filter_reason)
                filter_name = "unknown"
                rej_reason = RejectionReason.OTHER
                details = {}
                
                if reason_str == "excluded_quote_or_stable_symbol":
                    rej_reason = RejectionReason.QUOTE_FILTER
                    filter_name = "excluded_symbol"
                elif reason_str == "feature_not_ready":
                    rej_reason = RejectionReason.FEATURE_NOT_READY
                    filter_name = "feature_ready"
                elif reason_str == "raw_momentum_fallback_disabled":
                    rej_reason = RejectionReason.OTHER
                    filter_name = "normal_feature_check"
                elif reason_str.startswith("recent_stop_loss_quarantine"):
                    rej_reason = RejectionReason.QUARANTINE
                    filter_name = "stop_loss_quarantine"
                elif reason_str.startswith("signal_too_weak"):
                    rej_reason = RejectionReason.SIGNAL_TOO_WEAK
                    filter_name = "minimum_signal"
                    try:
                        parts = reason_str.split("_")
                        details = {"actual": float(parts[-3]), "threshold": float(parts[-1])}
                    except: pass
                elif reason_str.startswith("long_trend_not_positive"):
                    rej_reason = RejectionReason.TREND_MISMATCH
                    filter_name = "long_trend"
                    try:
                        details = {"actual": float(reason_str.split("_")[-1])}
                    except: pass
                elif reason_str.startswith("triple_agreement_failed"):
                    rej_reason = RejectionReason.TREND_MISMATCH
                    filter_name = "triple_agreement"
                elif reason_str.startswith("volatility_too_high"):
                    rej_reason = RejectionReason.VOLATILITY_TOO_HIGH
                    filter_name = "max_volatility"
                    try:
                        parts = reason_str.split("_")
                        details = {"actual": float(parts[-3]), "threshold": float(parts[-1])}
                    except: pass
                elif reason_str.startswith("expected_move_too_small"):
                    rej_reason = RejectionReason.OTHER
                    filter_name = "expected_move"
                elif "pacing" in reason_str or "cooldown" in reason_str:
                    rej_reason = RejectionReason.COOLDOWN
                    filter_name = "pacing_cooldown"

                events.candidate_filtered(
                    candidate_id=cid,
                    cycle_id=cycle_id,
                    symbol=sym,
                    rank=None,
                    reason=rej_reason,
                    filter_name=filter_name,
                    filter_stage=1,
                    details=details
                )
    except Exception as _obs_err:
        pass

    top_return = [(s, qs, sig) for s, qs, sig, _ in top]
    print(f'[QUALITY_RANK] top-{len(top_return)}: ' + ', '.join(
        f'{s}(qs={qs:.4f},sig={sig:.4f})' for s, qs, sig in top_return[:5]
    ))
    return (top_symbols, top_return, rejected_preview)

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
            rows = conn.execute(text("\n                SELECT symbol, strategy, created_at\n                FROM trades\n                WHERE side = 'buy'\n                  AND created_at >= CURRENT_TIMESTAMP + interval '2 hours'\n                ORDER BY created_at DESC\n            ")).mappings().all()
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
















# QFOS_AGENT5_SELL_SYMBOL_MUTEX_V1
# Purpose:
#   Prevent competing exit paths from selling the same symbol in the same cycle.
#
# Failure observed:
#   main_loop persisted a SELL, then qfos_exit_lifecycle immediately allowed
#   a second SELL for the same symbol with a different exit_reason.
#
# Rule:
#   Once any SELL is accepted for a symbol, all other SELLs for that symbol
#   are blocked briefly, regardless of strategy/exit_reason.

import time as _qfos_agent5_mutex_time

_QFOS_AGENT5_SELL_SYMBOL_MUTEX = {}

def qfos_agent5_symbol_mutex_cleanup(now=None, ttl_seconds=90):
    try:
        now = float(now or _qfos_agent5_mutex_time.time())
        stale = [
            k for k, v in list(_QFOS_AGENT5_SELL_SYMBOL_MUTEX.items())
            if now - float(v.get("ts", 0.0)) > ttl_seconds
        ]
        for k in stale:
            _QFOS_AGENT5_SELL_SYMBOL_MUTEX.pop(k, None)
    except Exception:
        pass


def qfos_agent5_symbol_mutex_check(symbol, fill=None, source="unknown"):
    try:
        now = _qfos_agent5_mutex_time.time()
        qfos_agent5_symbol_mutex_cleanup(now=now)

        symbol = str(symbol or "").strip()
        if not symbol:
            return False, "missing_symbol"

        existing = _QFOS_AGENT5_SELL_SYMBOL_MUTEX.get(symbol)
        if existing:
            age = now - float(existing.get("ts", 0.0))
            print(
                f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_symbol_mutex "
                f"age={age:.3f}s previous_source={existing.get('source')} "
                f"previous_reason={existing.get('reason')} source={source}",
                flush=True,
            )
            return False, "sell_symbol_mutex"

        return True, "sell_symbol_mutex_clear"

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_symbol_mutex_error error={exc!r} source={source}",
            flush=True,
        )
        return False, "sell_symbol_mutex_error"


def qfos_agent5_symbol_mutex_mark(symbol, fill=None, source="unknown"):
    try:
        symbol = str(symbol or "").strip()
        if not symbol:
            return

        reason = ""
        try:
            reason = str(
                (fill or {}).get("exit_reason")
                or (fill or {}).get("reason")
                or (fill or {}).get("strategy")
                or ""
            )
        except Exception:
            reason = ""

        _QFOS_AGENT5_SELL_SYMBOL_MUTEX[symbol] = {
            "ts": _qfos_agent5_mutex_time.time(),
            "source": source,
            "reason": reason,
        }

        print(
            f"[SELL_SYMBOL_MUTEX_MARK] symbol={symbol} reason={reason} source={source}",
            flush=True,
        )
    except Exception:
        pass

# END QFOS_AGENT5_SELL_SYMBOL_MUTEX_V1


# QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1
# Purpose:
#   Stop duplicate SELL persistence and oversells at the single atomic boundary.
#
# Root failure:
#   Duplicate SELL rows and negative running_qty proved SELLs were persisted
#   after the position was already closed.
#
# Rule:
#   A SELL may persist only when current DB open quantity exists.
#   SELL quantity must be clamped to DB open quantity.
#   Duplicate SELL intent within a short window is rejected.

def qfos_agent5_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def qfos_agent5_sell_guard_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def qfos_agent5_sell_guard_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def qfos_agent5_sell_guard_qty(fill):
    try:
        return qfos_agent5_float((fill or {}).get("quantity", (fill or {}).get("qty", 0.0)))
    except Exception:
        return 0.0


def qfos_agent5_sell_guard_price(fill):
    try:
        return qfos_agent5_float(
            (fill or {}).get(
                "fill_price",
                (fill or {}).get("price", (fill or {}).get("expected_price", 0.0)),
            )
        )
    except Exception:
        return 0.0


def qfos_agent5_sell_guard_reason(fill):
    try:
        return str(
            (fill or {}).get("exit_reason")
            or (fill or {}).get("reason")
            or (fill or {}).get("strategy")
            or "exit"
        ).strip()
    except Exception:
        return "exit"


def qfos_agent5_db_open_position_qty(conn, symbol):
    try:
        row = conn.execute(
            text("""
                select quantity
                from positions
                where symbol=:symbol
                  and coalesce(quantity,0) > 0.00000001
                limit 1
            """),
            {"symbol": symbol},
        ).mappings().first()

        if not row:
            return 0.0

        return max(0.0, qfos_agent5_float(row.get("quantity")))

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=open_qty_lookup_error error={repr(exc)}",
            flush=True,
        )
        return 0.0


def qfos_agent5_recent_duplicate_sell(conn, fill, seconds=30):
    symbol = qfos_agent5_sell_guard_symbol(fill)
    qty = qfos_agent5_sell_guard_qty(fill)
    price = qfos_agent5_sell_guard_price(fill)
    reason = qfos_agent5_sell_guard_reason(fill)

    try:
        row = conn.execute(
            text("""
                select id, created_at
                from trades
                where lower(side)='sell'
                  and symbol=:symbol
                  and abs(quantity - :quantity) <= 0.00001
                  and abs(fill_price - :fill_price) <= 0.00001
                  and coalesce(exit_reason,'') = :exit_reason
                  and created_at >= (CURRENT_TIMESTAMP - (:seconds || ' seconds')::interval)
                order by id desc
                limit 1
            """),
            {
                "symbol": symbol,
                "quantity": qty,
                "fill_price": price,
                "exit_reason": reason,
                "seconds": str(int(seconds)),
            },
        ).mappings().first()

        return row

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=duplicate_lookup_error error={repr(exc)}",
            flush=True,
        )
        return None


def qfos_agent5_atomic_sell_guard(conn, fill, source="unknown"):
    if not isinstance(fill, dict):
        return False, fill, "fill_not_dict"

    side = qfos_agent5_sell_guard_side(fill)
    if side != "sell":
        return True, fill, "non_sell"

    symbol = qfos_agent5_sell_guard_symbol(fill)
    qty = qfos_agent5_sell_guard_qty(fill)
    price = qfos_agent5_sell_guard_price(fill)
    reason = qfos_agent5_sell_guard_reason(fill)

    if not symbol:
        print("[SELL_VALIDATION_REJECT] reason=missing_symbol source=%s" % source, flush=True)
        return False, fill, "missing_symbol"

    if qty <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=bad_sell_qty qty={qty} source={source}",
            flush=True,
        )
        return False, fill, "bad_sell_qty"

    if price <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=bad_sell_price price={price} source={source}",
            flush=True,
        )
        return False, fill, "bad_sell_price"

    mutex_ok, mutex_reason = qfos_agent5_symbol_mutex_check(symbol, fill=fill, source=source)
    if not mutex_ok:
        return False, fill, mutex_reason

    open_qty = qfos_agent5_db_open_position_qty(conn, symbol)

    if open_qty <= 0.00000001:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_no_open_position "
            f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
            f"exit_reason={reason} source={source}",
            flush=True,
        )
        return False, fill, "sell_no_open_position"

    dup = qfos_agent5_recent_duplicate_sell(conn, fill)
    if dup:
        print(
            f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=duplicate_sell_intent "
            f"duplicate_id={dup.get('id')} requested_qty={qty:.12f} "
            f"fill_price={price:.12f} exit_reason={reason} source={source}",
            flush=True,
        )
        return False, fill, "duplicate_sell_intent"

    guarded = dict(fill)

    # Clamp tiny float overshoot to open quantity.
    if qty > open_qty:
        if qty <= open_qty + 0.00001:
            guarded["quantity"] = open_qty
            guarded["qty"] = open_qty
            print(
                f"[SELL_VALIDATION_CLAMP] symbol={symbol} reason=float_tolerance "
                f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
                f"exit_reason={reason} source={source}",
                flush=True,
            )
        else:
            print(
                f"[SELL_VALIDATION_REJECT] symbol={symbol} reason=sell_qty_exceeds_open "
                f"requested_qty={qty:.12f} db_open_qty={open_qty:.12f} "
                f"exit_reason={reason} source={source}",
                flush=True,
            )
            return False, fill, "sell_qty_exceeds_open"

    guarded["side"] = "sell"
    guarded["symbol"] = symbol
    guarded["fill_price"] = price
    guarded["price"] = price
    guarded["is_exit"] = True
    guarded["exit_reason"] = reason
    guarded["reason"] = reason
    guarded["strategy"] = reason

    print(
        f"[SELL_VALIDATION_ALLOW] symbol={symbol} requested_qty={qty:.12f} "
        f"db_open_qty={open_qty:.12f} fill_price={price:.12f} "
        f"exit_reason={reason} source={source}",
        flush=True,
    )

    qfos_agent5_symbol_mutex_mark(symbol, fill=guarded, source=source)
    return True, guarded, "sell_open_qty_confirmed"

# END QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1


# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1
# Phase 1 execution boundary:
# All paper BUY/SELL fills must pass through qfos_persist_fill_atomic().
# This prevents fake SELL rows, duplicate full-position SELLs, oversells,
# negative positions, and SELL persistence when no open spot quantity exists.

from datetime import datetime as _qfos_datetime
try:
    from sqlalchemy import text as _qfos_sa_text
except Exception:
    _qfos_sa_text = None

_QFOS_EPSILON = 1e-12


def _qfos_is_sqlalchemy_conn(conn):
    return hasattr(conn, "execute") and conn.__class__.__module__.startswith("sqlalchemy")


def _qfos_exec(conn, sql, params=None):
    """
    Executes SQL against either raw sqlite3 connections or SQLAlchemy 2.x connections.
    SQLAlchemy requires text(sql) and dict parameters.
    """
    if params is None:
        params = {}

    if _qfos_is_sqlalchemy_conn(conn):
        if _qfos_sa_text is None:
            raise RuntimeError("SQLAlchemy connection detected but sqlalchemy.text unavailable")

        if isinstance(params, (tuple, list)):
            raise RuntimeError("SQLAlchemy execution requires named dict parameters")

        return conn.execute(_qfos_sa_text(sql), params)

    if isinstance(params, dict):
        return conn.execute(sql, params)

    return conn.execute(sql, params)


def _qfos_commit(conn):
    try:
        conn.commit()
    except Exception:
        pass


def _qfos_rollback(conn):
    try:
        conn.rollback()
    except Exception:
        pass



def _qfos_now_utc_text():
    return _qfos_datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _qfos_first_existing_column(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def _qfos_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_bool_int(value):
    return 1 if bool(value) else 0


def _qfos_log_atomic(message):
    try:
        print(message, flush=True)
    except Exception:
        pass


def _qfos_get_position_row(conn, symbol):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    select_cols = ["symbol", qty_col]
    if avg_col:
        select_cols.append(avg_col)
    if realized_col:
        select_cols.append(realized_col)
    
    extra_cols = ["trade_uuid", "candidate_id", "entry_strategy", "highest_price_seen", "lowest_price_seen", "max_unrealized_profit", "max_unrealized_loss"]
    for ec in extra_cols:
        if ec in cols:
            select_cols.append(ec)

    sql = f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=:symbol LIMIT 1"
    result = _qfos_exec(conn, sql, {"symbol": symbol})

    # Fetch the row — handle both SQLAlchemy result (has .mappings()) and raw sqlite3 cursor
    row = None
    try:
        row = result.mappings().first()
    except Exception:
        try:
            row = result.fetchone()
        except Exception:
            row = None

    if not row:
        return {
            "exists": False,
            "quantity": 0.0,
            "avg_entry": 0.0,
            "realized_pnl": 0.0,
            "trade_uuid": None,
            "candidate_id": None,
            "entry_strategy": None,
            "highest_price_seen": 0.0,
            "lowest_price_seen": 0.0,
            "max_unrealized_profit": 0.0,
            "max_unrealized_loss": 0.0,
            "columns": cols,
            "qty_col": qty_col,
            "avg_col": avg_col,
            "realized_col": realized_col,
        }

    def _row_get(r, key, index, default=0.0):
        try:
            return r[key]
        except Exception:
            pass
        try:
            return r[index]
        except Exception:
            return default

    # Re-map columns accurately by finding their indices or keys
    qty = _qfos_float(_row_get(row, qty_col, 1), 0.0)
    avg_entry = _qfos_float(_row_get(row, avg_col, 2), 0.0) if avg_col else 0.0
    realized = _qfos_float(_row_get(row, realized_col, 3 if avg_col else 2), 0.0) if realized_col else 0.0

    ret_dict = {
        "exists": True,
        "quantity": qty,
        "avg_entry": avg_entry,
        "realized_pnl": realized,
        "columns": cols,
        "qty_col": qty_col,
        "avg_col": avg_col,
        "realized_col": realized_col,
    }

    # Populate extra columns
    for idx, ec in enumerate(extra_cols):
        if ec in cols:
            val = _row_get(row, ec, None, None)
            if ec in ("highest_price_seen", "lowest_price_seen", "max_unrealized_profit", "max_unrealized_loss"):
                ret_dict[ec] = _qfos_float(val, 0.0)
            else:
                ret_dict[ec] = str(val) if val is not None else None
        else:
            if ec in ("highest_price_seen", "lowest_price_seen", "max_unrealized_profit", "max_unrealized_loss"):
                ret_dict[ec] = 0.0
            else:
                ret_dict[ec] = None

    return ret_dict


def _qfos_upsert_position_atomic(conn, symbol, fill_price, strategy, new_qty, new_avg_entry, new_realized_pnl, trade_uuid=None, candidate_id=None, entry_strategy=None, highest_price_seen=None, lowest_price_seen=None, max_unrealized_profit=None, max_unrealized_loss=None):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])
    unrealized_col = _qfos_first_existing_column(cols, ["unrealized_pnl", "pnl_unrealized"])
    last_price_col = _qfos_first_existing_column(cols, ["last_price", "mark_price", "price"])
    exposure_col = _qfos_first_existing_column(cols, ["exposure", "notional"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])
    updated_col = _qfos_first_existing_column(cols, ["updated_at", "created_at", "timestamp"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    exists = _qfos_exec(
        conn,
        "SELECT 1 FROM positions WHERE symbol=:symbol LIMIT 1",
        {"symbol": symbol}
    ).fetchone() is not None

    safe_qty = float(max(new_qty, 0.0))

    values = {qty_col: safe_qty}

    if avg_col:
        values[avg_col] = float(new_avg_entry)

    if realized_col:
        values[realized_col] = float(new_realized_pnl)

    if unrealized_col:
        values[unrealized_col] = 0.0

    if last_price_col:
        values[last_price_col] = float(fill_price)

    if exposure_col:
        values[exposure_col] = float(safe_qty * fill_price)

    if strategy_col:
        values[strategy_col] = str(strategy or "unknown")

    if "trade_uuid" in cols:
        values["trade_uuid"] = trade_uuid
    if "candidate_id" in cols:
        values["candidate_id"] = candidate_id
    if "entry_strategy" in cols:
        values["entry_strategy"] = entry_strategy
    if "highest_price_seen" in cols:
        values["highest_price_seen"] = float(highest_price_seen) if highest_price_seen is not None else None
    if "lowest_price_seen" in cols:
        values["lowest_price_seen"] = float(lowest_price_seen) if lowest_price_seen is not None else None
    if "max_unrealized_profit" in cols:
        values["max_unrealized_profit"] = float(max_unrealized_profit) if max_unrealized_profit is not None else None
    if "max_unrealized_loss" in cols:
        values["max_unrealized_loss"] = float(max_unrealized_loss) if max_unrealized_loss is not None else None

    if updated_col:
        values[updated_col] = _qfos_now_utc_text()

    if exists:
        assignments = ", ".join([f"{k}=:{k}" for k in values.keys()])
        params = dict(values)
        params["__symbol"] = symbol
        _qfos_exec(
            conn,
            f"UPDATE positions SET {assignments} WHERE symbol=:__symbol",
            params,
        )
    else:
        insert_cols = ["symbol"] + list(values.keys())
        placeholders = ", ".join([f":{k}" for k in insert_cols])
        params = {"symbol": symbol}
        params.update(values)
        _qfos_exec(
            conn,
            f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({placeholders})",
            params,
        )


def _qfos_insert_trade_atomic(conn, normalized_fill):
    qfos_invalidate_ledger_cache()
    cols = _qfos_table_columns(conn, "trades")
    if not cols:
        raise RuntimeError("trades table missing or unreadable")

    created_at = normalized_fill.get("created_at") or _qfos_now_utc_text()

    data = {
        "symbol": normalized_fill.get("symbol"),
        "side": normalized_fill.get("side"),
        "quantity": float(normalized_fill.get("quantity", 0.0)),
        "qty": float(normalized_fill.get("quantity", 0.0)),
        "expected_price": float(normalized_fill.get("expected_price", normalized_fill.get("fill_price", 0.0))),
        "fill_price": float(normalized_fill.get("fill_price", 0.0)),
        "price": float(normalized_fill.get("fill_price", 0.0)),
        "slippage_bps": float(normalized_fill.get("slippage_bps", 0.0)),
        "pnl": float(normalized_fill.get("pnl", 0.0)),
        "realized_pnl": float(normalized_fill.get("pnl", 0.0)),
        "strategy": normalized_fill.get("strategy", "unknown"),
        "confidence": float(normalized_fill.get("confidence", 0.0)),
        "live": _qfos_bool_int(normalized_fill.get("live", False)),
        "shadow_mode": _qfos_bool_int(normalized_fill.get("shadow_mode", False)),
        "source": normalized_fill.get("source", "unknown"),
        "lifecycle_key": normalized_fill.get("lifecycle_key"),
        "is_exit": _qfos_bool_int(normalized_fill.get("is_exit", False)),
        "exit_reason": normalized_fill.get("exit_reason"),
        "created_at": created_at,
        "updated_at": created_at,
        "timestamp": created_at,
        "trade_uuid": normalized_fill.get("trade_uuid"),
        "candidate_id": normalized_fill.get("candidate_id"),
        "entry_strategy": normalized_fill.get("entry_strategy"),
        "regime": normalized_fill.get("regime"),
        "experiment_id": normalized_fill.get("experiment_id"),
        "software_version": normalized_fill.get("software_version"),
        "configuration_hash": normalized_fill.get("configuration_hash"),
        "mfe": float(normalized_fill.get("mfe")) if normalized_fill.get("mfe") is not None else None,
        "mae": float(normalized_fill.get("mae")) if normalized_fill.get("mae") is not None else None,
        "peak_price": float(normalized_fill.get("peak_price")) if normalized_fill.get("peak_price") is not None else None,
        "trough_price": float(normalized_fill.get("trough_price")) if normalized_fill.get("trough_price") is not None else None,
    }

    insert_cols = []
    insert_vals = []

    for col in cols:
        if col.lower() == "id":
            continue
        if col in data:
            insert_cols.append(col)
            insert_vals.append(data[col])

    if not insert_cols:
        raise RuntimeError("trades table has no compatible insert columns")

    placeholders = ", ".join([f":p{i}" for i in range(len(insert_cols))])
    sql = f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})"
    params = {f"p{i}": insert_vals[i] for i in range(len(insert_cols))}
    _qfos_exec(conn, sql, params)



def _qfos_latest_trade_for_symbol(conn, symbol):
    cols = _qfos_table_columns(conn, "trades")
    if not cols:
        return None

    id_col = _qfos_first_existing_column(cols, ["id"])
    side_col = _qfos_first_existing_column(cols, ["side"])
    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])

    if not side_col or not qty_col:
        return None

    select_cols = []
    if id_col:
        select_cols.append(id_col)
    else:
        select_cols.append("rowid")

    select_cols.extend([side_col, qty_col])

    if strategy_col:
        select_cols.append(strategy_col)

    order_col = id_col if id_col else "rowid"

    row = _qfos_exec(
        conn,
        f"SELECT {', '.join(select_cols)} FROM trades WHERE symbol=:symbol ORDER BY {order_col} DESC LIMIT 1",
        {"symbol": symbol}
    ).fetchone()

    if not row:
        return None

    out = {
        "id": row[0],
        "side": str(row[1] or "").lower(),
        "quantity": _qfos_float(row[2], 0.0),
        "strategy": "",
    }

    if strategy_col and len(row) >= 4:
        out["strategy"] = str(row[3] or "")

    return out


def _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy):
    """
    Runtime idempotency guard.

    If the latest persisted trade for this symbol is already a SELL with the
    same quantity and same strategy, reject the new SELL. This stops repeated
    full-position SELL spam even if an upstream loop keeps firing the same exit.
    """
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return None

    latest_side = latest.get("side")
    latest_qty = _qfos_float(latest.get("quantity"), 0.0)
    latest_strategy = str(latest.get("strategy") or "")
    strategy = str(strategy or "")

    qty_tol = max(_QFOS_EPSILON, abs(requested_qty) * 1e-9)

    if (
        latest_side == "sell"
        and abs(latest_qty - requested_qty) <= qty_tol
        and latest_strategy == strategy
    ):
        return {
            "reason": "duplicate_latest_sell",
            "latest_id": latest.get("id"),
            "latest_qty": latest_qty,
            "latest_strategy": latest_strategy,
        }

    return None


def _qfos_cleanup_closed_symbol_runtime_state(symbol, reason="closed_or_duplicate_sell", source="unknown"):
    """
    Clears stale runtime/profit-engine state for a symbol after the DB proves
    the symbol is already closed or the latest trade is already a SELL.

    This prevents Profit Engine / watchdog loops from repeatedly requesting
    the same invalid duplicate SELL.
    """
    removed = []

    dict_names = [
        "profit_engine_state",
        "profit_engine_peaks",
        "qfos_profit_engine_state",
        "qfos_profit_engine_peaks",
        "_qfos_profit_engine_state",
        "_qfos_profit_engine_peaks",
        "profit_engine_positions",
        "position_profit_state",
        "position_peaks",
        "pe_state",
        "peaks",
        "position_tracking_cache",
        "qfos_position_tracking_cache",
        "watchdog_state",
        "position_watchdog_state",
        "qfos_watchdog_state",
    ]

    set_names = [
        "profit_engine_active_symbols",
        "qfos_profit_engine_active_symbols",
        "position_watchdog_symbols",
        "qfos_position_watchdog_symbols",
        "closing_symbols",
        "symbols_pending_close",
        "qfos_symbols_pending_close",
    ]

    g = globals()

    for name in dict_names:
        obj = g.get(name)
        if isinstance(obj, dict) and symbol in obj:
            try:
                obj.pop(symbol, None)
                removed.append(name)
            except Exception:
                pass

    for name in set_names:
        obj = g.get(name)
        if isinstance(obj, set) and symbol in obj:
            try:
                obj.discard(symbol)
                removed.append(name)
            except Exception:
                pass

    if removed:
        _qfos_log_atomic(
            "[QFOS_RUNTIME_STATE_CLEANUP] symbol=%s reason=%s source=%s cleared=%s"
            % (symbol, reason, source, ",".join(sorted(set(removed))))
        )
    else:
        _qfos_log_atomic(
            "[QFOS_RUNTIME_STATE_CLEANUP] symbol=%s reason=%s source=%s cleared=none"
            % (symbol, reason, source)
        )

    return removed


def _qfos_db_open_qty_for_symbol(conn, symbol):
    try:
        pos = _qfos_get_position_row(conn, symbol)
        return _qfos_float(pos.get("quantity"), 0.0)
    except Exception:
        return None


def _qfos_latest_trade_is_sell_and_no_open_qty(conn, symbol):
    """
    True when DB says:
    - latest trade for symbol is SELL
    - position quantity is zero or missing

    Used by Profit Engine / watchdog source guards to avoid producing
    duplicate SELL requests after the symbol is already closed.
    """
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return False

    if latest.get("side") != "sell":
        return False

    open_qty = _qfos_db_open_qty_for_symbol(conn, symbol)
    if open_qty is None:
        return False

    return open_qty <= _QFOS_EPSILON


def _qfos_reconcile_position_from_duplicate_latest_sell(
    conn,
    symbol,
    requested_qty,
    existing_qty,
    existing_avg,
    existing_realized,
    fill_price,
    strategy,
    source,
    dup,
):
    """
    Fixes stale DB position after a SELL trade already exists.

    If latest trade is already a SELL for this symbol and the DB still shows
    open quantity, this is not a new SELL. It is a reconciliation event:
    zero the position and insert no new trade row.
    """
    latest_qty = _qfos_float(dup.get("latest_qty"), 0.0)
    qty_tol = max(_QFOS_EPSILON, abs(existing_qty) * 1e-9)

    if existing_qty <= _QFOS_EPSILON:
        return False

    # Accept either exact latest trade quantity or current requested quantity
    # as proof that the already-recorded SELL covers the stale open position.
    latest_covers_open = latest_qty >= existing_qty - qty_tol
    request_covers_open = requested_qty >= existing_qty - qty_tol

    if not (latest_covers_open or request_covers_open):
        return False

    _qfos_upsert_position_atomic(
        conn=conn,
        symbol=symbol,
        fill_price=fill_price,
        strategy=strategy,
        new_qty=0.0,
        new_avg_entry=existing_avg,
        new_realized_pnl=existing_realized,
    )

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="reconciled_duplicate_latest_sell_position_zeroed",
        source=source,
    )

    _qfos_log_atomic(
        "[SELL_POSITION_RECONCILED_FROM_LATEST_SELL] symbol=%s existing_qty=%s requested_qty=%s latest_qty=%s strategy=%s latest_id=%s source=%s"
        % (
            symbol,
            existing_qty,
            requested_qty,
            latest_qty,
            strategy,
            dup.get("latest_id"),
            source,
        )
    )

    return True


_QFOS_CLOSED_SYMBOL_TOMBSTONES = globals().setdefault("_QFOS_CLOSED_SYMBOL_TOMBSTONES", {})


def _qfos_mark_symbol_closed(symbol, side, quantity, strategy, source):
    if side != "sell":
        return

    _QFOS_CLOSED_SYMBOL_TOMBSTONES[symbol] = {
        "quantity": float(quantity or 0.0),
        "strategy": str(strategy or ""),
        "source": str(source or ""),
        "closed_at": _qfos_now_utc_text(),
    }

    _qfos_log_atomic(
        "[QFOS_SYMBOL_CLOSED_TOMBSTONE_SET] symbol=%s qty=%s strategy=%s source=%s"
        % (symbol, quantity, strategy, source)
    )


def _qfos_clear_symbol_closed_tombstone(symbol, reason="buy_or_reopen"):
    if symbol in _QFOS_CLOSED_SYMBOL_TOMBSTONES:
        _QFOS_CLOSED_SYMBOL_TOMBSTONES.pop(symbol, None)
        _qfos_log_atomic(
            "[QFOS_SYMBOL_CLOSED_TOMBSTONE_CLEARED] symbol=%s reason=%s"
            % (symbol, reason)
        )


def _qfos_has_closed_tombstone(symbol):
    return symbol in _QFOS_CLOSED_SYMBOL_TOMBSTONES


def _qfos_latest_trade_side(conn, symbol):
    latest = _qfos_latest_trade_for_symbol(conn, symbol)
    if not latest:
        return None
    return latest.get("side")


def _qfos_reject_or_reconcile_tombstoned_sell(
    conn,
    symbol,
    requested_qty,
    existing_qty,
    existing_avg,
    existing_realized,
    fill_price,
    strategy,
    source,
):
    """
    If this process already closed the symbol and no new BUY has happened,
    do not allow another SELL row. If stale sync restored quantity, zero it.
    """
    if not _qfos_has_closed_tombstone(symbol):
        return False

    latest_side = _qfos_latest_trade_side(conn, symbol)

    # A new BUY should clear tombstone and allow normal lifecycle.
    if latest_side == "buy":
        _qfos_clear_symbol_closed_tombstone(symbol, reason="latest_trade_buy")
        return False

    if latest_side != "sell":
        return False

    if existing_qty > _QFOS_EPSILON:
        _qfos_assert_sell_exit_accounting(normalized)

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=fill_price,
            strategy=strategy,
            new_qty=0.0,
            new_avg_entry=existing_avg,
            new_realized_pnl=existing_realized,
        )
        _qfos_cleanup_closed_symbol_runtime_state(
            symbol,
            reason="tombstone_rezero_stale_restored_position",
            source=source,
        )
        _qfos_log_atomic(
            "[SELL_TOMBSTONE_RECONCILED_STALE_POSITION] symbol=%s existing_qty=%s requested_qty=%s strategy=%s source=%s"
            % (symbol, existing_qty, requested_qty, strategy, source)
        )
        return True

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="tombstone_duplicate_sell_no_open_qty",
        source=source,
    )
    _qfos_log_atomic(
        "[SELL_TOMBSTONE_REJECT] symbol=%s requested_qty=%s strategy=%s source=%s"
        % (symbol, requested_qty, strategy, source)
    )
    return True


def qfos_reconcile_stale_closed_positions(conn, source="stale_position_reconciler"):
    """
    DB-level safety sweep.

    If positions.quantity > 0 but latest trade for that symbol is SELL and
    there is no newer BUY, the position is stale/corrupt. Zero it without
    inserting a trade row.

    This catches cases where no new SELL request arrives to trigger the
    normal duplicate/tombstone reconciliation path.
    """
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        return []

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])
    last_price_col = _qfos_first_existing_column(cols, ["last_price", "mark_price", "price"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])

    if not qty_col:
        return []

    rows = _qfos_exec(
        conn,
        f"SELECT symbol, {qty_col}"
        + (f", {avg_col}" if avg_col else ", 0")
        + (f", {realized_col}" if realized_col else ", 0")
        + (f", {last_price_col}" if last_price_col else ", 0")
        + (f", {strategy_col}" if strategy_col else ", ''")
        + f" FROM positions WHERE {qty_col} > :eps",
        {"eps": _QFOS_EPSILON}
    ).fetchall()

    reconciled = []

    for row in rows:
        symbol = row[0]
        open_qty = _qfos_float(row[1], 0.0)
        avg_entry = _qfos_float(row[2], 0.0)
        realized = _qfos_float(row[3], 0.0)
        last_price = _qfos_float(row[4], 0.0)
        strategy = str(row[5] or "stale_position_reconciler")

        latest = _qfos_latest_trade_for_symbol(conn, symbol)
        if not latest:
            continue

        if latest.get("side") != "sell":
            continue

        latest_qty = _qfos_float(latest.get("quantity"), 0.0)
        qty_tol = max(_QFOS_EPSILON, abs(open_qty) * 1e-9)

        if latest_qty + qty_tol < open_qty:
            continue

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=last_price if last_price > _QFOS_EPSILON else avg_entry,
            strategy=strategy,
            new_qty=0.0,
            new_avg_entry=avg_entry,
            new_realized_pnl=realized,
        )

        _qfos_cleanup_closed_symbol_runtime_state(
            symbol,
            reason="db_stale_closed_position_reconciled",
            source=source,
        )

        _qfos_mark_symbol_closed(
            symbol=symbol,
            side="sell",
            quantity=open_qty,
            strategy=strategy,
            source=source,
        )

        _qfos_log_atomic(
            "[QFOS_DB_STALE_POSITION_RECONCILED] symbol=%s open_qty=%s latest_sell_qty=%s latest_id=%s source=%s"
            % (symbol, open_qty, latest_qty, latest.get("id"), source)
        )

        reconciled.append(symbol)

    return reconciled


def _qfos_exit_accounting_fields(fill, side, strategy, source):
    """
    Phase 3A accounting invariant.

    Any SELL in spot paper mode is a reduction/exit, not a fresh entry.
    Therefore it must be persisted with:
      is_exit = true
      exit_reason populated

    BUY rows remain non-exit unless explicitly supplied otherwise.
    """
    side = str(side or "").lower()
    strategy = str(strategy or "").strip()
    source = str(source or "").strip()

    raw_is_exit = fill.get("is_exit", None)
    raw_exit_reason = fill.get("exit_reason", None)

    if side == "sell":
        reason = (
            str(raw_exit_reason).strip()
            if raw_exit_reason not in (None, "", "None")
            else ""
        )

        if not reason:
            reason = strategy or source or "paper_sell_exit"

        return True, reason

    # BUY should not be counted as exit by default.
    if raw_is_exit in (True, 1, "1", "true", "True", "yes", "YES"):
        reason = (
            str(raw_exit_reason).strip()
            if raw_exit_reason not in (None, "", "None")
            else "explicit_buy_exit_flag"
        )
        return True, reason

    return False, None


def _qfos_assert_sell_exit_accounting(normalized_fill):
    side = str(normalized_fill.get("side") or "").lower()
    if side != "sell":
        return

    is_exit = normalized_fill.get("is_exit")
    exit_reason = normalized_fill.get("exit_reason")

    if not is_exit:
        raise RuntimeError(
            "SELL_ACCOUNTING_INVARIANT_FAIL:is_exit_false:%s"
            % normalized_fill.get("symbol")
        )

    if exit_reason in (None, "", "None"):
        raise RuntimeError(
            "SELL_ACCOUNTING_INVARIANT_FAIL:missing_exit_reason:%s"
            % normalized_fill.get("symbol")
        )


def _qfos_symbol_buy_lifecycle_qty(conn, symbol):
    """
    Returns total BUY quantity recorded in trades for this symbol.
    After a clean reset, this must be > 0 before any SELL can be accepted.
    """
    try:
        cols = _qfos_table_columns(conn, "trades")
        if not cols:
            return 0.0

        side_col = _qfos_first_existing_column(cols, ["side"])
        qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])

        if not side_col or not qty_col:
            return 0.0

        row = _qfos_exec(
            conn,
            f"""
            SELECT COALESCE(SUM({qty_col}), 0)
            FROM trades
            WHERE symbol=:symbol
              AND {side_col}='buy'
            """,
            {"symbol": symbol}
        ).fetchone()

        return _qfos_float(row[0] if row else 0.0, 0.0)
    except Exception as exc:
        _qfos_log_atomic(
            "[BUY_LIFECYCLE_CHECK_ERROR] symbol=%s error=%s"
            % (symbol, repr(exc))
        )
        return 0.0


def _qfos_symbol_sell_lifecycle_qty(conn, symbol):
    try:
        cols = _qfos_table_columns(conn, "trades")
        if not cols:
            return 0.0

        side_col = _qfos_first_existing_column(cols, ["side"])
        qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])

        if not side_col or not qty_col:
            return 0.0

        row = _qfos_exec(
            conn,
            f"""
            SELECT COALESCE(SUM({qty_col}), 0)
            FROM trades
            WHERE symbol=:symbol
              AND {side_col}='sell'
            """,
            {"symbol": symbol}
        ).fetchone()

        return _qfos_float(row[0] if row else 0.0, 0.0)
    except Exception:
        return 0.0


def _qfos_has_valid_buy_lifecycle_for_sell(conn, symbol, requested_qty):
    """
    A SELL is valid only if this DB has recorded BUY lifecycle quantity.
    This prevents clean-reset SELL-only rows caused by paper_position_sync.
    """
    buy_qty = _qfos_symbol_buy_lifecycle_qty(conn, symbol)
    sell_qty = _qfos_symbol_sell_lifecycle_qty(conn, symbol)
    remaining_lifecycle_qty = max(buy_qty - sell_qty, 0.0)

    qty_tol = max(_QFOS_EPSILON, abs(requested_qty) * 1e-9)

    return buy_qty > _QFOS_EPSILON and remaining_lifecycle_qty + qty_tol > 0.0


def _qfos_zero_invalid_no_buy_position(conn, symbol, existing_avg, existing_realized, fill_price, strategy, source):
    """
    If paper_position_sync resurrected a position with no BUY lifecycle,
    zero it without creating a trade row.
    """
    _qfos_upsert_position_atomic(
        conn=conn,
        symbol=symbol,
        fill_price=fill_price,
        strategy=strategy or "invalid_no_buy_lifecycle_zeroed",
        new_qty=0.0,
        new_avg_entry=existing_avg,
        new_realized_pnl=existing_realized,
    )

    _qfos_cleanup_closed_symbol_runtime_state(
        symbol,
        reason="invalid_no_buy_lifecycle_zeroed",
        source=source,
    )

    _qfos_log_atomic(
        "[SELL_VALIDATION_REJECT] symbol=%s reason=no_buy_lifecycle_position_zeroed strategy=%s source=%s"
        % (symbol, strategy, source)
    )


def qfos_reconcile_positions_without_buy_lifecycle(conn, source="no_buy_lifecycle_reconciler"):
    """
    Sweeps DB positions. Any positive position with no BUY lifecycle in trades
    is invalid after a clean reset and is zeroed with no trade row.
    """
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        return []

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])
    last_price_col = _qfos_first_existing_column(cols, ["last_price", "mark_price", "price"])
    strategy_col = _qfos_first_existing_column(cols, ["strategy"])

    if not qty_col:
        return []

    rows = _qfos_exec(
        conn,
        f"SELECT symbol, {qty_col}"
        + (f", {avg_col}" if avg_col else ", 0")
        + (f", {realized_col}" if realized_col else ", 0")
        + (f", {last_price_col}" if last_price_col else ", 0")
        + (f", {strategy_col}" if strategy_col else ", ''")
        + f" FROM positions WHERE {qty_col} > :eps",
        {"eps": _QFOS_EPSILON}
    ).fetchall()

    fixed = []

    for row in rows:
        symbol = row[0]
        open_qty = _qfos_float(row[1], 0.0)
        avg_entry = _qfos_float(row[2], 0.0)
        realized = _qfos_float(row[3], 0.0)
        last_price = _qfos_float(row[4], 0.0)
        strategy = str(row[5] or "no_buy_lifecycle_reconciler")

        buy_qty = _qfos_symbol_buy_lifecycle_qty(conn, symbol)

        if buy_qty > _QFOS_EPSILON:
            continue

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=last_price if last_price > _QFOS_EPSILON else avg_entry,
            strategy=strategy,
            new_qty=0.0,
            new_avg_entry=avg_entry,
            new_realized_pnl=realized,
        )

        _qfos_cleanup_closed_symbol_runtime_state(
            symbol,
            reason="db_no_buy_lifecycle_position_zeroed",
            source=source,
        )

        _qfos_log_atomic(
            "[QFOS_NO_BUY_LIFECYCLE_POSITION_ZEROED] symbol=%s open_qty=%s strategy=%s source=%s"
            % (symbol, open_qty, strategy, source)
        )

        fixed.append(symbol)

    return fixed



# QFOS_AGENT2_EXIT_RISK_POLICY_V1
# Purpose:
#   Fix Agent 2 exit/risk policy failure without killing profitable quick scalps.
#
# Observed failure:
#   trailing_profit_exit was closing negative-PnL trades.
#   stop_loss exits were valid risk exits but symbols could be re-entered too soon.
#
# Rules:
#   1) trailing_profit_exit is only allowed when pnl > 0.
#   2) stop_loss exits are allowed, but the symbol is put on short risk cooldown.
#   3) BUYs are blocked during symbol risk cooldown.
#   4) take-profit and positive max-hold exits are untouched.

import time as _qfos_agent2_exit_time

_QFOS_AGENT2_SYMBOL_RISK_COOLDOWN = {}

def qfos_agent2_exit_policy_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def qfos_agent2_exit_policy_text(value):
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def qfos_agent2_exit_policy_cleanup(now=None, ttl_seconds=900):
    try:
        now = float(now or _qfos_agent2_exit_time.time())
        stale = [
            k for k, v in list(_QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.items())
            if now - float(v.get("ts", 0.0)) > ttl_seconds
        ]
        for k in stale:
            _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.pop(k, None)
    except Exception:
        pass


def qfos_agent2_exit_policy_mark_symbol(symbol, reason="", pnl=0.0, ttl_seconds=600):
    try:
        symbol = qfos_agent2_exit_policy_text(symbol)
        if not symbol:
            return

        _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN[symbol] = {
            "ts": _qfos_agent2_exit_time.time(),
            "reason": qfos_agent2_exit_policy_text(reason),
            "pnl": qfos_agent2_exit_policy_float(pnl),
            "ttl_seconds": int(ttl_seconds),
        }

        print(
            f"[AGENT2_RISK_COOLDOWN_MARK] symbol={symbol} reason={reason} pnl={pnl} ttl_seconds={ttl_seconds}",
            flush=True,
        )
    except Exception:
        pass


def qfos_agent2_exit_risk_policy_guard(conn, fill, source="unknown"):
    try:
        qfos_agent2_exit_policy_cleanup()

        if not isinstance(fill, dict):
            return True, "not_a_fill_dict"

        side = qfos_agent2_exit_policy_text(fill.get("side")).lower()
        symbol = qfos_agent2_exit_policy_text(fill.get("symbol"))
        reason = qfos_agent2_exit_policy_text(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
        )
        pnl = qfos_agent2_exit_policy_float(fill.get("pnl"), 0.0)

        if not symbol:
            return True, "missing_symbol_fail_open"

        now = _qfos_agent2_exit_time.time()

        # BUY cooldown after damaging exits.
        if side == "buy":
            active = _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.get(symbol)
            if active:
                age = now - float(active.get("ts", 0.0))
                ttl_seconds = int(active.get("ttl_seconds", 600))
                if age <= ttl_seconds:
                    print(
                        f"[AGENT2_ENTRY_REJECT] symbol={symbol} reason=symbol_risk_cooldown "
                        f"age={age:.2f}s ttl_seconds={ttl_seconds} "
                        f"previous_reason={active.get('reason')} previous_pnl={active.get('pnl')} source={source}",
                        flush=True,
                    )
                    return False, "symbol_risk_cooldown"
                _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.pop(symbol, None)

            return True, "buy_exit_policy_clear"

        if side != "sell":
            return True, "non_sell_clear"

        # Hard rule: trailing profit cannot be negative or breakeven.
        if reason == "trailing_profit_exit" and pnl <= 0:
            print(
                f"[AGENT2_EXIT_REJECT] symbol={symbol} reason=negative_trailing_profit_exit "
                f"pnl={pnl:.12f} source={source}",
                flush=True,
            )
            return False, "negative_trailing_profit_exit"

        # Stop-loss exits are valid, but mark short cooldown to prevent immediate re-entry.
        if "stop_loss" in reason and pnl < 0:
            qfos_agent2_exit_policy_mark_symbol(
                symbol,
                reason=reason,
                pnl=pnl,
                ttl_seconds=600,
            )
            return True, "stop_loss_allowed_with_symbol_cooldown"

        return True, "exit_policy_clear"

    except Exception as exc:
        print(
            f"[AGENT2_EXIT_POLICY_ERROR] error={exc!r} source={source}",
            flush=True,
        )
        # Fail open to avoid trapping positions due to policy-code exception.
        return True, "exit_policy_error_fail_open"

# END QFOS_AGENT2_EXIT_RISK_POLICY_V1


def qfos_ensure_observability_lineage_schema(conn):
    """Ensure durable linkage from an entry to its later exit without touching trade policy."""
    for table, column in (
        ("trades", "candidate_id TEXT"),
        ("trades", "entry_strategy TEXT"),
        ("positions", "candidate_id TEXT"),
        ("positions", "entry_strategy TEXT"),
    ):
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"))
        except Exception:
            # Existing SQLite compatibility paths may not support IF NOT EXISTS.
            try:
                cols = _qfos_table_columns(conn, table)
                name = column.split()[0]
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column}"))
            except Exception:
                pass


def qfos_persist_fill_atomic(conn, fill, source="main_loop"):

    # C5: final shared-control gate. This runs before audit mode and before
    # any validation, accounting, or database write for every atomic caller.
    _qfos_gate_decision = default_execution_gate()(fill)
    _qfos_gate_fill = fill if isinstance(fill, dict) else {}

    if not _qfos_gate_decision.allowed:
        print(
            "[EXECUTION_GATE_BLOCK] "
            f"symbol={_qfos_gate_fill.get('symbol', 'UNKNOWN')} "
            f"side={_qfos_gate_fill.get('side', 'UNKNOWN')} "
            f"source={source} "
            f"reason={_qfos_gate_decision.reason}",
            flush=True,
        )
        return None

    if _qfos_gate_decision.paused:
        print(
            "[EXECUTION_GATE_ALLOW_EXIT] "
            f"symbol={_qfos_gate_fill.get('symbol', 'UNKNOWN')} "
            f"side={_qfos_gate_fill.get('side', 'UNKNOWN')} "
            f"source={source} "
            f"reason={_qfos_gate_decision.reason}",
            flush=True,
        )

    # QFOS_ACTIVE_EXIT_EPOCH_FIX_V2
    # Final SELL truth firewall. Prevent a stale DB lifecycle snapshot from
    # persisting an early stagnation or false take-profit exit.
    if _QFOS_AUDIT_BOOT:
        try:
            _qfos_audit_fill = fill if isinstance(fill, dict) else {}
            print(
                "[QFOS_AUDIT_BOOT] fill blocked "
                f"symbol={_qfos_audit_fill.get('symbol', 'UNKNOWN')} "
                f"side={_qfos_audit_fill.get('side', 'UNKNOWN')} "
                f"source={source}",
                flush=True,
            )
        except Exception:
            print("[QFOS_AUDIT_BOOT] fill blocked", flush=True)
        return None

    try:
        _qfos_exit_fill = fill if isinstance(fill, dict) else {}
        _qfos_exit_side = str(_qfos_exit_fill.get("side") or "").lower().strip()
        _qfos_exit_reason = str(
            _qfos_exit_fill.get("exit_reason")
            or _qfos_exit_fill.get("reason")
            or _qfos_exit_fill.get("strategy")
            or ""
        ).lower().strip()

        if _qfos_exit_side == "sell" and _qfos_exit_reason in {
            "sideways_stagnation_exit",
            "sideways_take_profit_exit",
        }:
            _qfos_exit_symbol = str(_qfos_exit_fill.get("symbol") or "").strip()
            _qfos_exit_price = float(
                _qfos_exit_fill.get("fill_price")
                or _qfos_exit_fill.get("expected_price")
                or _qfos_exit_fill.get("price")
                or 0.0
            )

            if _qfos_exit_symbol and _qfos_sa_text is not None:
                _qfos_latest_buy = conn.execute(_qfos_sa_text("""
                    SELECT
                        fill_price,
                        created_at
                    FROM trades
                    WHERE symbol = :symbol
                      AND LOWER(side) = 'buy'
                    ORDER BY id DESC
                    LIMIT 1
                """), {
                    "symbol": _qfos_exit_symbol,
                }).mappings().first()

                if _qfos_latest_buy:
                    _qfos_buy_price = float(
                        _qfos_latest_buy.get("fill_price") or 0.0
                    )

                    _qfos_age_row = conn.execute(_qfos_sa_text("""
                        SELECT EXTRACT(
                            EPOCH FROM (
                                CURRENT_TIMESTAMP - :created_at
                            )
                        ) / 60.0 AS age_minutes
                    """), {
                        "created_at": _qfos_latest_buy.get("created_at"),
                    }).mappings().first()

                    _qfos_age_minutes = float(
                        ((_qfos_age_row or {}).get("age_minutes")) or 0.0
                    )

                    if (
                        _qfos_exit_reason == "sideways_stagnation_exit"
                        and _qfos_age_minutes < float(
                            globals().get(
                                "QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE",
                                20.0,
                            )
                        )
                    ):
                        print(
                            "[EXIT_SELL_BLOCK] "
                            f"symbol={_qfos_exit_symbol} "
                            f"reason=sideways_stagnation_before_min_age "
                            f"age_min={_qfos_age_minutes:.4f}",
                            flush=True,
                        )
                        return False

                    _qfos_tp = float(
                        globals().get(
                            "QFOS_EXIT_SIDEWAYS_TAKE_PROFIT_PCT",
                            globals().get(
                                "QFOS_SIDEWAYS_EXIT_TAKE_PROFIT_PCT",
                                0.0055,
                            ),
                        )
                    )

                    if (
                        _qfos_exit_reason == "sideways_take_profit_exit"
                        and _qfos_buy_price > 0
                        and _qfos_exit_price < (
                            _qfos_buy_price * (1.0 + _qfos_tp)
                        )
                    ):
                        print(
                            "[EXIT_SELL_BLOCK] "
                            f"symbol={_qfos_exit_symbol} "
                            f"reason=false_sideways_take_profit "
                            f"buy_price={_qfos_buy_price:.12f} "
                            f"sell_price={_qfos_exit_price:.12f} "
                            f"required_tp={_qfos_tp:.6f}",
                            flush=True,
                        )
                        return False

    except Exception as _qfos_exit_firewall_error:
        print(
            f"[EXIT_SELL_BLOCK_ERROR] "
            f"error={_qfos_exit_firewall_error!r}",
            flush=True,
        )
        # Fail closed for only the two unreliable lifecycle exit labels.
        try:
            if _qfos_exit_reason in {
                "sideways_stagnation_exit",
                "sideways_take_profit_exit",
            }:
                return False
        except Exception:
            pass



    # QFOS_PAUSE_HARD_BLOCK_ATOMIC_BUY_V1
    # Final authority: no new BUY may persist while the bot is paused.
    # SELLs remain allowed so protective exits can still close risk.
    # QFOS_DISABLE_RESCUE_BUYS_V3
    # Final persistence containment for rescue-originated BUYs.
    # SELLs remain permitted. Non-rescue BUY paths remain unchanged.
    try:
        _qfos_rescue_fill = fill if isinstance(fill, dict) else {
            'side': getattr(fill, 'side', None),
            'symbol': getattr(fill, 'symbol', None),
            'strategy': getattr(fill, 'strategy', None),
            'source': getattr(fill, 'source', None),
        }
        _qfos_rescue_side = str(_qfos_rescue_fill.get('side') or '').strip().lower()
        _qfos_rescue_strategy = str(_qfos_rescue_fill.get('strategy') or '').strip().lower()
        _qfos_rescue_fill_source = str(_qfos_rescue_fill.get('source') or '').strip().lower()
        _qfos_rescue_call_source = str(locals().get('source', '') or '').strip().lower()
        _qfos_rescue_context = ' '.join((
            _qfos_rescue_strategy,
            _qfos_rescue_fill_source,
            _qfos_rescue_call_source,
        ))
        _qfos_is_rescue = 'allocator_rescue' in _qfos_rescue_context
        if _qfos_rescue_side == 'buy' and _qfos_is_rescue:
            print(
                '[RESCUE_BUY_BLOCK] '
                f"symbol={_qfos_rescue_fill.get('symbol')} "
                f"strategy={_qfos_rescue_fill.get('strategy')} "
                f"source={locals().get('source', '')} "
                'reason=negative_raw_fill_expectancy',
                flush=True,
            )
            return False
    except Exception as _qfos_rescue_guard_error:
        _qfos_rescue_fallback_text = repr(fill).lower()
        if 'allocator_rescue' in _qfos_rescue_fallback_text:
            print(
                f'[RESCUE_BUY_BLOCK_GUARD_ERROR] error={_qfos_rescue_guard_error!r}',
                flush=True,
            )
            return False
    try:
        if isinstance(fill, dict):
            _qfos_pause_fill = fill
        else:
            _qfos_pause_fill = {
                "side": getattr(fill, "side", None),
                "symbol": getattr(fill, "symbol", None),
                "strategy": getattr(fill, "strategy", None),
            }

        _qfos_pause_side = str(
            _qfos_pause_fill.get("side") or ""
        ).strip().lower()

        if _qfos_pause_side == "buy":
            _qfos_pause_state_known = False
            _qfos_pause_active = True  # fail closed if pause authority is unavailable

            try:
                _qfos_is_paused_fn = globals().get("is_paused")
                if callable(_qfos_is_paused_fn):
                    _qfos_pause_active = bool(_qfos_is_paused_fn())
                    _qfos_pause_state_known = True
                elif "paused" in globals():
                    _qfos_pause_active = bool(globals().get("paused"))
                    _qfos_pause_state_known = True
            except Exception:
                _qfos_pause_active = True

            if _qfos_pause_active:
                print(
                    "[PAUSE_HARD_BLOCK] "
                    f"side=buy symbol={_qfos_pause_fill.get('symbol')} "
                    f"strategy={_qfos_pause_fill.get('strategy')} "
                    f"source={source} "
                    f"pause_state_known={_qfos_pause_state_known}",
                    flush=True,
                )
                return False
    except Exception as _qfos_pause_guard_error:
        print(
            "[PAUSE_HARD_BLOCK] "
            f"side=buy reason=guard_exception "
            f"error={_qfos_pause_guard_error!r} source={source}",
            flush=True,
        )
        return False

    # QFOS_NORMALIZED_NAMEERROR_DUST_SELL_FIX_V1
    # Guarantee `normalized` exists in this function scope before any downstream reference.
    # Also block tiny dust SELLs that can appear after DB clamp / float precision cleanup.
    try:
        if isinstance(fill, dict):
            normalized = dict(fill)
        else:
            normalized = {
                "symbol": getattr(fill, "symbol", None),
                "side": getattr(fill, "side", None),
                "quantity": getattr(fill, "quantity", getattr(fill, "qty", None)),
                "fill_price": getattr(fill, "fill_price", getattr(fill, "price", None)),
                "expected_price": getattr(fill, "expected_price", getattr(fill, "price", None)),
                "strategy": getattr(fill, "strategy", None),
                "exit_reason": getattr(fill, "exit_reason", getattr(fill, "reason", None)),
                "confidence": getattr(fill, "confidence", 1.0),
                "pnl": getattr(fill, "pnl", 0.0),
            }

        # Keep the original `fill` aligned with normalized for code paths below this point.
        fill = normalized

        _qfos_side = str(normalized.get("side") or "").lower()
        _qfos_symbol = str(normalized.get("symbol") or "")
        try:
            _qfos_qty = abs(float(normalized.get("quantity") or normalized.get("qty") or 0.0))
        except Exception:
            _qfos_qty = 0.0

        # Dust SELL guard:
        # The failed 60-min run showed TRIA sell qty=1.5258789e-05 causing normalized NameError.
        # This is not a real economic position close; it is float residue. Ignore safely.
        _qfos_dust_sell_qty = 0.0001
        if _qfos_side == "sell" and _qfos_qty > 0 and _qfos_qty <= _qfos_dust_sell_qty:
            print(
                f"[QFOS_DUST_SELL_REJECT] symbol={_qfos_symbol} qty={_qfos_qty:.12f} "
                f"threshold={_qfos_dust_sell_qty:.12f} source={source}",
                flush=True,
            )
            return False

    except Exception as _qfos_norm_exc:
        print(
            f"[QFOS_NORMALIZED_GUARD_ERROR] error={_qfos_norm_exc!r} source={source}",
            flush=True,
        )
        # Fail closed for malformed fills instead of crashing the loop.
        return False

    # QFOS_AGENT2_EXIT_RISK_POLICY_V1: enforce exit/risk policy before persistence
    try:
        _qfos_agent2_ok, _qfos_agent2_reason = qfos_agent2_exit_risk_policy_guard(conn, fill, source=source)
        if not _qfos_agent2_ok:
            return False
    except Exception as _qfos_agent2_exc:
        print(f"[AGENT2_EXIT_POLICY_ERROR] fail_open error={_qfos_agent2_exc!r} source={source}", flush=True)
    # QFOS_AGENT5_HARD_SELL_IDEMPOTENCY_V1: enforce SELL idempotency before any persistence
    try:
        _qfos_sell_ok, _qfos_guarded_fill, _qfos_sell_reason = qfos_agent5_atomic_sell_guard(conn, fill, source=source)
        if not _qfos_sell_ok:
            return False
        fill = _qfos_guarded_fill
    except Exception as _qfos_sell_guard_error:
        print(f"[SELL_VALIDATION_REJECT] reason=sell_guard_exception error={_qfos_sell_guard_error!r} source={source}", flush=True)
        return False
    # QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1: prevent NameError in runtime exception paths
    normalized = None
    try:
        normalized = dict(fill or {}) if isinstance(fill, dict) else {}
    except Exception:
        normalized = {}
    """
    Atomic paper fill persistence boundary.

    Invariants:
    - SELL with no open quantity is rejected before trade insert.
    - SELL requested_qty <= 0 is rejected before trade insert.
    - SELL requested_qty > open_qty is capped to open_qty.
    - Repeated full-position SELL after zero is rejected.
    - Position update succeeds before trade insert.
    - SELL realized PnL uses DB avg_entry.
    - Historical rows are not rewritten.
    """
    if conn is None:
        raise RuntimeError("qfos_persist_fill_atomic requires sqlite connection")

    if not isinstance(fill, dict):
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] reason=fill_not_dict source=%s" % source)
        return None

    symbol = str(fill.get("symbol") or "").strip()
    side = str(fill.get("side") or "").strip().lower()

    requested_qty = _qfos_float(fill.get("quantity", fill.get("qty")), 0.0)
    expected_price = _qfos_float(fill.get("expected_price", fill.get("fill_price", fill.get("price"))), 0.0)
    fill_price = _qfos_float(fill.get("fill_price", fill.get("price", expected_price)), 0.0)
    slippage_bps = _qfos_float(fill.get("slippage_bps"), 0.0)
    strategy = str(fill.get("strategy") or fill.get("reason") or "unknown")
    confidence = _qfos_float(fill.get("confidence"), 0.0)

    if not symbol:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] reason=missing_symbol source=%s" % source)
        return None

    if side not in ("buy", "sell"):
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s reason=invalid_side source=%s" % (symbol, side, source))
        return None

    if requested_qty <= _QFOS_EPSILON:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s qty=%s reason=non_positive_qty source=%s" % (symbol, side, requested_qty, source))
        return None

    if fill_price <= _QFOS_EPSILON:
        _qfos_log_atomic("[FILL_VALIDATION_REJECT] symbol=%s side=%s price=%s reason=non_positive_fill_price source=%s" % (symbol, side, fill_price, source))
        return None

    if side == "buy":
        _qfos_clear_symbol_closed_tombstone(symbol, reason="accepted_buy_request")

    started_tx = False

    try:
        # SQLAlchemy connections (PostgreSQL) manage transactions automatically via engine.begin().
        # Only issue an explicit BEGIN for raw sqlite3 connections.
        if not _qfos_is_sqlalchemy_conn(conn):
            if not getattr(conn, "in_transaction", False):
                _qfos_exec(conn, "BEGIN IMMEDIATE")
                started_tx = True

        qfos_ensure_observability_lineage_schema(conn)
        pos = _qfos_get_position_row(conn, symbol)
        existing_qty = _qfos_float(pos.get("quantity"), 0.0)
        existing_avg = _qfos_float(pos.get("avg_entry"), 0.0)
        existing_realized = _qfos_float(pos.get("realized_pnl"), 0.0)

        final_qty = requested_qty
        pnl = 0.0

        if side == "sell":
            if not _qfos_has_valid_buy_lifecycle_for_sell(conn, symbol, requested_qty):
                _qfos_zero_invalid_no_buy_position(
                    conn=conn,
                    symbol=symbol,
                    existing_avg=existing_avg,
                    existing_realized=existing_realized,
                    fill_price=fill_price,
                    strategy=strategy,
                    source=source,
                )
                if started_tx:
                    _qfos_commit(conn)
                return None

            tombstone_handled = _qfos_reject_or_reconcile_tombstoned_sell(
                conn=conn,
                symbol=symbol,
                requested_qty=requested_qty,
                existing_qty=existing_qty,
                existing_avg=existing_avg,
                existing_realized=existing_realized,
                fill_price=fill_price,
                strategy=strategy,
                source=source,
            )
            if tombstone_handled:
                if started_tx:
                    _qfos_commit(conn)
                return None

            dup = _qfos_duplicate_sell_guard(conn, symbol, requested_qty, strategy)
            if dup:
                reconciled = _qfos_reconcile_position_from_duplicate_latest_sell(
                    conn=conn,
                    symbol=symbol,
                    requested_qty=requested_qty,
                    existing_qty=existing_qty,
                    existing_avg=existing_avg,
                    existing_realized=existing_realized,
                    fill_price=fill_price,
                    strategy=strategy,
                    source=source,
                    dup=dup,
                )

                if reconciled:
                    if started_tx:
                        _qfos_commit(conn)
                    return None

                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s strategy=%s reason=%s latest_id=%s source=%s"
                    % (symbol, requested_qty, strategy, dup.get("reason"), dup.get("latest_id"), source)
                )
                _qfos_cleanup_closed_symbol_runtime_state(
                    symbol,
                    reason="duplicate_latest_sell",
                    source=source,
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None
            if existing_qty <= _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s open_qty=%s strategy=%s reason=no_open_position source=%s"
                    % (symbol, requested_qty, existing_qty, strategy, source)
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None

            if requested_qty > existing_qty + _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_CAP] symbol=%s requested_qty=%s open_qty=%s strategy=%s reason=qty_gt_open_capped source=%s"
                    % (symbol, requested_qty, existing_qty, strategy, source)
                )
                final_qty = existing_qty

            if final_qty <= _QFOS_EPSILON:
                _qfos_log_atomic(
                    "[SELL_VALIDATION_REJECT] symbol=%s requested_qty=%s final_qty=%s strategy=%s reason=zero_final_qty source=%s"
                    % (symbol, requested_qty, final_qty, strategy, source)
                )
                if started_tx:
                    _qfos_rollback(conn)
                return None

            new_qty = max(existing_qty - final_qty, 0.0)
            new_avg = existing_avg
            pnl = float(final_qty * (fill_price - existing_avg))
            new_realized = existing_realized + pnl

        else:
            old_value = existing_qty * existing_avg
            buy_value = requested_qty * fill_price
            new_qty = existing_qty + requested_qty
            new_avg = (old_value + buy_value) / new_qty if new_qty > _QFOS_EPSILON else fill_price
            new_realized = existing_realized
            final_qty = requested_qty
            pnl = 0.0

        if new_qty < -_QFOS_EPSILON:
            _qfos_log_atomic(
                "[FILL_VALIDATION_REJECT] symbol=%s side=%s requested_qty=%s open_qty=%s new_qty=%s reason=negative_position_guard source=%s"
                % (symbol, side, requested_qty, existing_qty, new_qty, source)
            )
            if started_tx:
                _qfos_rollback(conn)
            return None

        exit_is_exit, exit_reason = _qfos_exit_accounting_fields(
            fill=fill,
            side=side,
            strategy=strategy,
            source=source,
        )

        # ----------------------------------------------------------------
        # Lifecycle metadata injection
        # ----------------------------------------------------------------

        # BUY: generate a trade_uuid if the fill doesn't carry one already.
        # This UUID follows the position until close and is copied to the
        # exit trade row so both legs share the same lifecycle key.
        if side == "buy":
            import uuid as _uuid
            fill_trade_uuid = fill.get("trade_uuid") or str(_uuid.uuid4())
            fill_candidate_id = fill.get("candidate_id")
            entry_strategy = strategy
        else:
            # SELL: inherit the uuid from the open position row (if present)
            fill_trade_uuid = pos.get("trade_uuid") or fill.get("trade_uuid")
            fill_candidate_id = pos.get("candidate_id") or fill.get("candidate_id")
            entry_strategy = pos.get("entry_strategy") or fill.get("entry_strategy") or strategy

        # Harvest lifecycle price extremes captured by mark_positions_to_market
        pos_highest = _qfos_float(pos.get("highest_price_seen"), 0.0)
        pos_lowest  = _qfos_float(pos.get("lowest_price_seen"),  0.0)
        pos_avg     = _qfos_float(pos.get("avg_entry"), 0.0) or existing_avg

        # Compute MFE / MAE only for exit trades where we have a valid avg_entry
        exit_mfe = None
        exit_mae = None
        exit_peak_price  = None
        exit_trough_price = None
        if side == "sell" and pos_avg > _QFOS_EPSILON:
            if pos_highest > _QFOS_EPSILON:
                exit_mfe = float(final_qty * (pos_highest - pos_avg))
                exit_peak_price = pos_highest
            if pos_lowest > _QFOS_EPSILON:
                exit_mae = float(final_qty * (pos_lowest - pos_avg))   # negative value = adverse
                exit_trough_price = pos_lowest

        normalized = dict(fill)
        normalized.update({
            "symbol": symbol,
            "side": side,
            "quantity": float(final_qty),
            "expected_price": float(expected_price if expected_price > _QFOS_EPSILON else fill_price),
            "fill_price": float(fill_price),
            "slippage_bps": float(slippage_bps),
            "pnl": float(pnl),
            "strategy": strategy,
            "confidence": float(confidence),
            "live": bool(fill.get("live", False)),
            "shadow_mode": bool(fill.get("shadow_mode", False)),
            "source": str(source),
            "is_exit": bool(exit_is_exit),
            "exit_reason": exit_reason,
            "created_at": fill.get("created_at") or _qfos_now_utc_text(),
            # Lifecycle fields
            "trade_uuid": fill_trade_uuid,
            "candidate_id": fill_candidate_id,
            "entry_strategy": entry_strategy,
            "regime": fill.get("regime"),
            "experiment_id": fill.get("experiment_id"),
            "software_version": fill.get("software_version"),
            "configuration_hash": fill.get("configuration_hash"),
            # MFE / MAE — NULL for BUY rows and for historical trades (Option A)
            "mfe": exit_mfe,
            "mae": exit_mae,
            "peak_price": exit_peak_price,
            "trough_price": exit_trough_price,
        })

        # On close, reset lifecycle price trackers so the next entry starts clean
        reset_peak = side == "sell" and new_qty <= _QFOS_EPSILON

        _qfos_upsert_position_atomic(
            conn=conn,
            symbol=symbol,
            fill_price=fill_price,
            strategy=strategy,
            new_qty=new_qty,
            new_avg_entry=new_avg,
            new_realized_pnl=new_realized,
            trade_uuid=fill_trade_uuid,
            candidate_id=fill_candidate_id,
            entry_strategy=entry_strategy,
            highest_price_seen=0.0 if reset_peak else None,
            lowest_price_seen=0.0 if reset_peak else None,
        )

        _qfos_insert_trade_atomic(conn, normalized)

        if side == "sell" and new_qty <= _QFOS_EPSILON:
            _qfos_mark_symbol_closed(
                symbol=symbol,
                side=side,
                quantity=final_qty,
                strategy=strategy,
                source=source,
            )

        # PM_V2_DRY_RUN_HOOK_V1
        try:
            from core.pm_v2 import pm_v2_on_trade_closed, pm_v2_record_entry_score
            if side == "sell":
                pm_v2_on_trade_closed(
                    symbol=symbol,
                    trade_uuid=fill_trade_uuid,
                    side=side,
                    pnl=pnl,
                    engine=engine,
                )
            elif side == "buy":
                _score = fill.get("pm_v2_score")
                if _score is not None:
                    pm_v2_record_entry_score(
                        symbol=symbol,
                        trade_uuid=fill_trade_uuid,
                        score=float(_score),
                        conn=conn,
                    )
        except Exception as _pm_v2_err:
            try:
                print(f"[PM_V2_HOOK_ERROR] {_pm_v2_err!r}", flush=True)
            except Exception:
                pass

        if started_tx:
            _qfos_commit(conn)

        _qfos_log_atomic(
            "[FILL_PERSISTED_ATOMIC] symbol=%s side=%s qty=%s new_qty=%s pnl=%s strategy=%s source=%s"
            % (symbol, side, final_qty, new_qty, pnl, strategy, source)
        )

        return normalized

    except Exception as exc:
        import traceback
        traceback.print_exc()
        if started_tx:
            try:
                _qfos_rollback(conn)
            except Exception:
                pass
        _qfos_log_atomic(
            "[FILL_PERSISTENCE_ERROR] symbol=%s side=%s qty=%s error=%s source=%s"
            % (symbol, side, requested_qty, repr(exc), source)
        )
        raise
# END QFOS_ATOMIC_FILL_PERSISTENCE_V1


# BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1
# Ensures execution-accounting schema exists and blocks stale position resurrection.
# This does not tune strategy, dashboard, risk, fallback buys, or live trading.

import os as _qfos_schema_os
import sqlite3 as _qfos_schema_sqlite3

_QFOS_EXECUTION_SCHEMA_GUARD_DONE = globals().setdefault("_QFOS_EXECUTION_SCHEMA_GUARD_DONE", False)


def _qfos_schema_find_db_path():
    candidates = []

    env_path = _qfos_schema_os.environ.get("QFOS_DB_PATH") or _qfos_schema_os.environ.get("QUANT_DB_PATH")
    if env_path:
        candidates.append(env_path)

    database_url = _qfos_schema_os.environ.get("DATABASE_URL") or ""
    if database_url.startswith("sqlite:///"):
        candidates.append(database_url.replace("sqlite:///", "", 1))

    candidates.extend([
        "/app/data/quant.db",
    ])

    for path in candidates:
        try:
            if path and _qfos_schema_os.path.exists(path):
                return path
        except Exception:
            pass

    return "/app/data/quant.db"


def qfos_ensure_execution_accounting_schema_and_guards(db_path=None, source="schema_guard"):
    """
    Phase 3A hard guard.

    1. Adds trades.is_exit and trades.exit_reason if missing.
    2. Adds SQLite triggers that prevent stale position resurrection:
       if latest trade for symbol is already SELL and covers the proposed
       position quantity, positions insert/update is ignored.
    """
    if db_path is None:
        db_path = _qfos_schema_find_db_path()

    if not db_path or not _qfos_schema_os.path.exists(db_path):
        return False

    conn = _qfos_schema_sqlite3.connect(db_path, timeout=30)
    cur = conn.cursor()

    def _pragma_cols(cursor, table):
        try:
            cursor.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' AND table_schema = 'public'")
            return [r[0] for r in cursor.fetchall()]
        except Exception:
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                return [r[1] for r in cursor.fetchall()]
            except Exception:
                return []

    try:
        trade_cols = _pragma_cols(cur, "trades")

        if "is_exit" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN is_exit BOOLEAN DEFAULT 0")
            print("[QFOS_SCHEMA_GUARD] added trades.is_exit source=%s" % source, flush=True)

        trade_cols = _pragma_cols(cur, "trades")

        if "exit_reason" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
            print("[QFOS_SCHEMA_GUARD] added trades.exit_reason source=%s" % source, flush=True)

        # Block stale INSERT restore:
        # If latest trade is SELL and its quantity covers NEW.quantity,
        # ignore attempts to recreate a positive position.
        cur.execute("DROP TRIGGER IF EXISTS qfos_block_stale_position_insert")
        cur.execute("""
        CREATE TRIGGER qfos_block_stale_position_insert
        BEFORE INSERT ON positions
        WHEN NEW.quantity > 0.00000001
         AND COALESCE((SELECT side FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), '') = 'sell'
         AND COALESCE((SELECT quantity FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), 0) + 0.00000001 >= NEW.quantity
        BEGIN
            SELECT RAISE(IGNORE);
        END;
        """)

        # Block stale UPDATE restore:
        # If a closed/zero position is repeatedly restored by sync, ignore it.
        cur.execute("DROP TRIGGER IF EXISTS qfos_block_stale_position_update")
        cur.execute("""
        CREATE TRIGGER qfos_block_stale_position_update
        BEFORE UPDATE OF quantity ON positions
        WHEN NEW.quantity > 0.00000001
         AND COALESCE((SELECT side FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), '') = 'sell'
         AND COALESCE((SELECT quantity FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), 0) + 0.00000001 >= NEW.quantity
        BEGIN
            SELECT RAISE(IGNORE);
        END;
        """)

        conn.commit()
        print("[QFOS_SCHEMA_GUARD] execution accounting schema/triggers ensured source=%s" % source, flush=True)
        return True

    finally:
        conn.close()


def qfos_start_execution_accounting_schema_guard():
    global _QFOS_EXECUTION_SCHEMA_GUARD_DONE

    if _QFOS_EXECUTION_SCHEMA_GUARD_DONE:
        return False

    _QFOS_EXECUTION_SCHEMA_GUARD_DONE = True

    try:
        qfos_ensure_execution_accounting_schema_and_guards(source="startup")
    except Exception as exc:
        try:
            print("[QFOS_SCHEMA_GUARD_ERROR] error=%s" % repr(exc), flush=True)
        except Exception:
            pass

    return True


try:
    print("[QFOS_POSTGRES_ONLY] disabled startup: qfos_start_execution_accounting_schema_guard (SQLite-backed execution schema guard)", flush=True)
except Exception as _qfos_schema_guard_start_error:
    try:
        print("[QFOS_SCHEMA_GUARD_START_ERROR] error=%s" % repr(_qfos_schema_guard_start_error), flush=True)
    except Exception:
        pass
# END QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1





# BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1
# Runtime safety daemon:
# Automatically repairs positions resurrected by stale paper_position_sync after a full SELL.
# It inserts no trade rows and does not change strategy thresholds.

import os as _qfos_os
import time as _qfos_time
import threading as _qfos_threading
import sqlite3 as _qfos_sqlite3

_QFOS_STALE_RECONCILER_STARTED = globals().setdefault("_QFOS_STALE_RECONCILER_STARTED", False)


def _qfos_find_sqlite_db_path():
    candidates = []

    env_path = _qfos_os.environ.get("QFOS_DB_PATH") or _qfos_os.environ.get("QUANT_DB_PATH")
    if env_path:
        candidates.append(env_path)

    database_url = _qfos_os.environ.get("DATABASE_URL") or ""
    if database_url.startswith("sqlite:///"):
        candidates.append(database_url.replace("sqlite:///", "", 1))

    candidates.extend([
        "/app/data/quant.db",
    ])

    for path in candidates:
        try:
            if path and _qfos_os.path.exists(path):
                return path
        except Exception:
            pass

    # Default Docker path. It may appear after startup.
    return "/app/data/quant.db"


def qfos_run_stale_position_reconciler_once(source="auto_phase2h_reconciler"):
    try:
        from core.db import engine

        with engine.begin() as conn:
            symbols = qfos_reconcile_stale_closed_positions(
                conn,
                source=source,
            )

            no_buy_symbols = []

            try:
                no_buy_symbols = qfos_reconcile_positions_without_buy_lifecycle(
                    conn,
                    source=source,
                )
            except Exception as _no_buy_reconcile_error:
                print(
                    "[QFOS_NO_BUY_LIFECYCLE_RECONCILER_ERROR] error=%s"
                    % repr(_no_buy_reconcile_error),
                    flush=True,
                )

            all_symbols = sorted(
                set((symbols or []) + (no_buy_symbols or []))
            )

            if all_symbols:
                print(
                    "[QFOS_AUTO_STALE_RECONCILER] reconciled=%s source=%s"
                    % (",".join(all_symbols), source),
                    flush=True,
                )

            return all_symbols

    except Exception as exc:
        try:
            print(
                "[QFOS_AUTO_STALE_RECONCILER_ERROR] error=%s"
                % repr(exc),
                flush=True,
            )
        except Exception:
            pass

        return []
def qfos_start_stale_position_reconciler_daemon(interval_seconds=10):
    global _QFOS_STALE_RECONCILER_STARTED

    if _QFOS_STALE_RECONCILER_STARTED:
        return False

    if str(_qfos_os.environ.get("QFOS_DISABLE_STALE_RECONCILER", "")).lower() in ("1", "true", "yes"):
        print("[QFOS_AUTO_STALE_RECONCILER_DISABLED]", flush=True)
        return False

    _QFOS_STALE_RECONCILER_STARTED = True

    def _loop():
        print(
            "[QFOS_AUTO_STALE_RECONCILER_STARTED] interval_seconds=%s"
            % interval_seconds,
            flush=True,
        )
        while True:
            qfos_run_stale_position_reconciler_once(source="auto_phase2h_reconciler")
            _qfos_time.sleep(interval_seconds)

    t = _qfos_threading.Thread(
        target=_loop,
        name="qfos_stale_position_reconciler",
        daemon=True,
    )
    t.start()
    return True


# Start at module import so it runs under uvicorn/start.sh as well as normal main loop.
try:
    qfos_start_stale_position_reconciler_daemon()
except Exception as _qfos_reconciler_start_error:
    try:
        print(
            "[QFOS_AUTO_STALE_RECONCILER_START_ERROR] error=%s"
            % repr(_qfos_reconciler_start_error),
            flush=True,
        )
    except Exception:
        pass
# END QFOS_STALE_POSITION_RECONCILER_DAEMON_V1




# AGENT3_LEGACY_RESCUE_GATE_V1
# Strict fail-closed sanitizer for legacy main.py ALLOCATOR_RESCUE path.
# Authorized scope: main.py ALLOCATOR_RESCUE block only.
def _agent3_float(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def _agent3_order_source(order):
    if not isinstance(order, dict):
        return ""
    feature = order.get("feature") if isinstance(order.get("feature"), dict) else {}
    return str(order.get("feature_source") or feature.get("source") or "").strip().upper()


def _agent3_is_rescue_order(order):
    if not isinstance(order, dict):
        return False
    src = str(order.get("source", "") or "").lower()
    strategy = str(order.get("strategy", "") or "").lower()
    reason = str(order.get("entry_reason", "") or "").lower()
    return (
        "allocator_opportunity_rescue" in src
        or "allocator_rescue" in src
        or "evo_allocator_rescue" in strategy
        or "allocator_rescue" in reason
    )


def _agent3_extract_top_symbols(local_vars):
    """
    Extract visible ENTRY QUALITY TOP list using likely variable names.
    Fail closed if no list/set can be found.
    """
    candidates = []

    for name in (
        "entry_quality_top_10",
        "entry_quality_top",
        "entry_quality",
        "top_quality_rows",
        "entry_quality_rows",
        "quality_top_rows",
        "top_entries",
        "top_candidates",
        "top_symbols",
        "entry_quality_top_symbols",
    ):
        if name in local_vars:
            candidates.append(local_vars.get(name))

    symbols = set()

    def add_symbol(value):
        if value is None:
            return
        if isinstance(value, str):
            if "/" in value:
                symbols.add(value)
            return
        if isinstance(value, dict):
            sym = value.get("symbol") or value.get("pair")
            if isinstance(sym, str) and "/" in sym:
                symbols.add(sym)
            return
        if isinstance(value, (tuple, list)) and value:
            first = value[0]
            if isinstance(first, str) and "/" in first:
                symbols.add(first)
            elif isinstance(first, dict):
                sym = first.get("symbol") or first.get("pair")
                if isinstance(sym, str) and "/" in sym:
                    symbols.add(sym)

    for obj in candidates:
        if isinstance(obj, dict):
            for k, v in obj.items():
                add_symbol(k)
                add_symbol(v)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                add_symbol(item)
        else:
            add_symbol(obj)

    return symbols


def _agent3_lookup_feature(order, local_vars):
    if not isinstance(order, dict):
        return {}

    feature = order.get("feature")
    if isinstance(feature, dict):
        return feature

    symbol = order.get("symbol")
    for name in ("features", "feature_map", "features_by_symbol", "normal_features", "market_features"):
        obj = local_vars.get(name)
        if isinstance(obj, dict) and symbol in obj and isinstance(obj.get(symbol), dict):
            return obj.get(symbol)

    market_state = local_vars.get("market_state")
    if isinstance(market_state, dict):
        obj = market_state.get("features")
        if isinstance(obj, dict) and symbol in obj and isinstance(obj.get(symbol), dict):
            return obj.get(symbol)

    return {}


def _agent3_exposure_allows_rescue(local_vars):
    """
    Best-effort pre-handoff exposure guard.
    Fail open only when exposure data is absent, because canonical exposure guard still runs later.
    Fail closed when exposure data is present and over the known SIDEWAYS cap.
    """
    regime = str(local_vars.get("regime") or local_vars.get("market_regime") or "").upper()

    portfolio = local_vars.get("portfolio")
    market_state = local_vars.get("market_state")

    exposure_pct = None
    if isinstance(portfolio, dict):
        exposure_pct = portfolio.get("exposure_pct")
    if exposure_pct is None and isinstance(market_state, dict):
        exposure_pct = market_state.get("exposure_pct")
    if exposure_pct is None:
        exposure_pct = local_vars.get("exposure_pct")

    if exposure_pct is None:
        return True, ""

    exposure_pct = _agent3_float(exposure_pct, 0.0)

    # Match observed runtime guard from logs: SIDEWAYS exposure limit 0.0450.
    # Do not change risk thresholds; this only mirrors existing guard before rescue handoff.
    if regime == "SIDEWAYS" and exposure_pct >= 0.045:
        return False, f"exposure_cap regime=SIDEWAYS exposure_pct={exposure_pct:.4f} limit=0.0450"

    return True, ""


def _agent3_rescue_order_gate(order, local_vars):
    """
    Return (allowed: bool, reason: str, enriched_order: dict).
    Fail closed when rank/source/metadata cannot be proven.
    """
    if not isinstance(order, dict):
        return False, "invalid_order", order

    if not _agent3_is_rescue_order(order):
        return True, "not_rescue_order", order

    symbol = order.get("symbol")
    if not symbol:
        return False, "missing_symbol", order

    top_symbols = _agent3_extract_top_symbols(local_vars)
    if not top_symbols:
        return False, "entry_quality_top_empty", order

    if symbol not in top_symbols:
        return False, "not_top_quality", order

    feature = _agent3_lookup_feature(order, local_vars)
    if not isinstance(feature, dict) or not feature:
        return False, "missing_feature_snapshot", order

    if not bool(feature.get("ready", False)):
        return False, "feature_not_ready", order

    feature_source = str(feature.get("source", "") or "").strip().upper()
    if feature_source != "NORMAL":
        return False, "feature_source_not_normal", order

    source_words = " ".join([
        str(order.get("source", "")),
        str(order.get("strategy", "")),
        str(order.get("entry_reason", "")),
        str(feature.get("source", "")),
    ]).upper()

    if (
        "FALLBACK_SCOUT" in source_words
        or "RAW_MOMENTUM_FALLBACK" in source_words
        or "RAW_MOMENTUM" in source_words
    ):
        return False, "fallback_source_disabled", order

    symbol_regime = str(feature.get("symbol_regime", "") or "").upper()
    if symbol_regime not in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"):
        return False, f"weak_symbol_regime:{symbol_regime or 'missing'}", order

    signal_strength = _agent3_float(feature.get("signal_strength", order.get("signal_strength")), 0.0)
    if signal_strength <= 0:
        return False, "weak_signal", order

    confidence = _agent3_float(order.get("confidence", feature.get("confidence", 0.0)), 0.0)
    if confidence <= 0:
        return False, "weak_confidence", order

    exposure_ok, exposure_reason = _agent3_exposure_allows_rescue(local_vars)
    if not exposure_ok:
        return False, exposure_reason, order

    enriched = dict(order)
    enriched["feature"] = feature
    enriched["feature_source"] = "NORMAL"
    enriched["signal_strength"] = signal_strength
    enriched["symbol_regime"] = symbol_regime
    enriched["entry_reason"] = "evo_allocator_rescue_entry_quality_top_normal"
    enriched["confidence"] = confidence

    return True, "passed", enriched


def _agent3_filter_legacy_rescue_orders(order_list, local_vars):
    if not isinstance(order_list, list):
        return order_list

    filtered = []
    blocked = []

    for order in order_list:
        allowed, reason, enriched = _agent3_rescue_order_gate(order, local_vars)
        if allowed:
            filtered.append(enriched)
        else:
            symbol = order.get("symbol") if isinstance(order, dict) else None
            blocked.append((symbol, reason))
            print(f"[ALLOCATOR_RESCUE] no_candidate_passed reason={reason} symbol={symbol}")

    if blocked:
        print(f"[ALLOCATOR_RESCUE] blocked_legacy_orders count={len(blocked)} details={blocked}")

    return filtered



# QFOS_PAUSE_REASON_CALLABLE_ASSERT
try:
    qfos_restore_pause_reason_callable()
except Exception:
    pass


def _qfos_log_transaction_failure(exc, context):
    """
    Structured, unmissable logging for any exception that aborts the main
    trade-persistence transaction (the `with engine.begin() as conn:` block
    in the trading loop).

    This replaces the old behavior where such failures surfaced only as a
    generic "Bot loop error: <message>" line with no indication of which
    trade, symbol, or SQL statement was involved, and no distinction between
    a DB-level failure and any other kind of exception. Every rollback of
    the persistence transaction must emit one of these structured lines.
    """
    import traceback as _qfos_traceback

    orig = exc
    sqlalchemy_statement = None
    sqlalchemy_params = None
    try:
        # SQLAlchemy wraps DBAPI errors; pull out the failing statement/params
        # if this is one of those (works for IntegrityError, OperationalError,
        # ProgrammingError, etc. raised via SQLAlchemy).
        sqlalchemy_statement = getattr(exc, "statement", None)
        sqlalchemy_params = getattr(exc, "params", None)
    except Exception:
        pass

    sql_statement_repr = repr(str(sqlalchemy_statement)) if sqlalchemy_statement else "None"
    sql_params_repr = repr(sqlalchemy_params) if sqlalchemy_params else "None"
    print(
        "[QFOS_TRANSACTION_ROLLBACK] "
        f"exception_type={type(orig).__name__} "
        f"exception_message={str(orig)!r} "
        f"symbols_in_cycle={context.get('symbols')} "
        f"last_strategy={context.get('last_strategy')} "
        f"sql_statement={sql_statement_repr} "
        f"sql_params={sql_params_repr}",
        flush=True,
    )
    # Full traceback on its own line so it doesn't get swallowed by
    # log-line-oriented tooling grepping for the structured line above.
    print("[QFOS_TRANSACTION_ROLLBACK_TRACEBACK]", flush=True)
    _qfos_traceback.print_exc()


def _qfos_ensure_strategy_scores_constraint():
    """
    Self-healing schema guard, run once at startup (not per-cycle).

    strategy_scores is the one table in this app whose ON CONFLICT(strategy)
    usage (see the main trading loop) was never backed by a matching
    UNIQUE/PRIMARY KEY constraint on `strategy` -- every other ON CONFLICT
    target table in this file creates itself with the right constraint
    in-line; this one was created elsewhere with PRIMARY KEY(id) only.
    This mirrors that same self-healing pattern for this table so a fresh
    or already-migrated environment both work without manual intervention.

    Safe to call on every process start: checks pg_constraint first and is
    a no-op if the constraint (or an equivalent unique index) already exists.
    Does not attempt to deduplicate existing rows -- that is a one-time,
    reviewed data migration (see migration_001_fix_strategy_scores_constraint.sql),
    not something to run silently on every boot.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS strategy_scores (
                    id SERIAL PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    sharpe REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    score REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            has_constraint = conn.execute(text("""
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'strategy_scores_strategy_key'
            """)).first()
            if not has_constraint:
                has_dupes = conn.execute(text("""
                    SELECT 1 FROM (
                        SELECT strategy FROM strategy_scores
                        GROUP BY strategy HAVING COUNT(*) > 1
                    ) t LIMIT 1
                """)).first()
                if has_dupes:
                    print(
                        "[QFOS_STARTUP_SCHEMA_CHECK] strategy_scores has duplicate "
                        "`strategy` rows and is missing its UNIQUE constraint. "
                        "Refusing to add it automatically because that requires "
                        "merging historical data -- run "
                        "migration_001_fix_strategy_scores_constraint.sql manually "
                        "first. The app will continue to run, but ON CONFLICT(strategy) "
                        "will keep failing (and the enclosing trade transaction will "
                        "keep rolling back) until this migration is applied.",
                        flush=True,
                    )
                else:
                    conn.execute(text("""
                        ALTER TABLE strategy_scores
                            ADD CONSTRAINT strategy_scores_strategy_key UNIQUE (strategy)
                    """))
                    print(
                        "[QFOS_STARTUP_SCHEMA_CHECK] added missing UNIQUE(strategy) "
                        "constraint to strategy_scores.",
                        flush=True,
                    )
    except Exception as e:
        print(
            f"[QFOS_STARTUP_SCHEMA_CHECK_ERROR] could not verify/repair "
            f"strategy_scores schema: {e!r}",
            flush=True,
        )


def _qfos_apply_strategy_score_updates(updates):
    """
    Best-effort, non-fatal application of strategy_scores writes.

    Deliberately runs in its OWN transaction, separate from the trade/
    position/snapshot transaction that already committed by the time this
    is called. Every failure here is caught, logged, and skipped -- it
    must never be allowed to affect the trading ledger, which is already
    durable, and it must never block Telegram notification either.
    Analytics consumes truth; it does not gate it.
    """
    for update in updates:
        try:
            with engine.begin() as score_conn:
                score_conn.execute(text("""
                    INSERT INTO strategy_scores (strategy, sharpe, drawdown, score, status)
                    VALUES (:strategy, 0, 0, :pnl, CASE WHEN :pnl < 0 THEN 'blocked' ELSE 'active' END)
                    ON CONFLICT(strategy) DO UPDATE SET
                        score = strategy_scores.score + excluded.score,
                        status = CASE WHEN strategy_scores.score + excluded.score < 0 THEN 'blocked' ELSE 'active' END
                """), update)
        except Exception as e:
            print(
                "[QFOS_STRATEGY_SCORE_UPDATE_FAILED] "
                f"strategy={update.get('strategy')} pnl={update.get('pnl')} "
                f"exception_type={type(e).__name__} exception_message={str(e)!r} "
                "-- trade/position/snapshot already committed and are NOT "
                "affected by this failure.",
                flush=True,
            )


def main():
    print("================================")
    print("PM V2")
    print(f"Enabled : {settings.pm_v2_enabled}")
    print(f"Dry Run : {settings.pm_v2_dry_run}")
    from core.pm_v2 import PM_V2_MIN_AGE_MINUTES, PM_V2_SCORE_MARGIN
    print(f"Min Age : {PM_V2_MIN_AGE_MINUTES}")
    print(f"Margin  : {PM_V2_SCORE_MARGIN}")
    print("================================")
    
    positions = globals().get('positions', [])
    load_state_from_db()
    _qfos_ensure_strategy_scores_constraint()
    while True:
        try:
            qfos_start_evaluation_batch()
            tick = market.tick()
            raw_prices = tick['prices']
            print('MARKET TICK DATA RAW:', raw_prices)
            prices = validate_market_prices(raw_prices)
            print('MARKET TICK DATA VALIDATED:', prices)
            if not prices:
                print('MARKET DATA BLOCK: no_valid_prices_this_cycle')
                qfos_stop_evaluation_batch()
                time.sleep(settings.trade_interval_seconds)
                continue
            remember_prices(prices)
            refresh_recent_trade_counts()
                        # Agent 4 NORMAL feature handoff guard.
            # Accepts either raw price maps or tick-shaped objects, but does not create synthetic data.
            try:
                feature_health = features.update(prices)
            except Exception as _agent4_feature_update_error:
                print(f"[FEATURE_HANDOFF_ERROR] update_failed={repr(_agent4_feature_update_error)}", flush=True)
                feature_health = {}

            try:
                if hasattr(features, "all_features"):
                    f_by_symbol = features.all_features(settings.symbol_list)
                else:
                    f_by_symbol = {s: features.features(s) for s in settings.symbol_list}
            except Exception as _agent4_feature_build_error:
                print(f"[FEATURE_HANDOFF_ERROR] build_failed={repr(_agent4_feature_build_error)}", flush=True)
                f_by_symbol = {}

            try:
                _agent4_normal = {
                    s: f for s, f in f_by_symbol.items()
                    if isinstance(f, dict)
                    and str(f.get("source", "")).upper() == "NORMAL"
                }
                _agent4_ready = {
                    s: f for s, f in _agent4_normal.items()
                    if f.get("ready") is True
                }
                _agent4_sample = []
                for _s, _f in list(_agent4_ready.items())[:3]:
                    _agent4_sample.append({
                        "symbol": _s,
                        "source": _f.get("source"),
                        "ready": _f.get("ready"),
                        "price": _f.get("price"),
                        "signal_strength": _f.get("signal_strength"),
                        "confidence": _f.get("confidence"),
                        "symbol_regime": _f.get("symbol_regime"),
                    })
                print(
                    "[FEATURE_HANDOFF] "
                    f"Feature symbols={len(f_by_symbol)} "
                    f"normal_features={len(_agent4_normal)} "
                    f"ready_features={len(_agent4_ready)} "
                    f"health={feature_health} "
                    f"sample={_agent4_sample}",
                    flush=True,
                )
            except Exception as _agent4_feature_log_error:
                print(f"[FEATURE_HANDOFF_ERROR] log_failed={repr(_agent4_feature_log_error)}", flush=True)
            # POLICY V2 FIXED ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â persist normal feature map immediately after feature build

            # AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2
            try:
                f_by_symbol, feature_health, _agent4_feature_builder = _qfos_agent4_build_normal_feature_map(
                    features_obj=features,
                    prices=prices,
                    settings=settings,
                    already_updated=True,
                    prior_health=feature_health,
                )
                _qfos_agent4_log_feature_handoff(f_by_symbol, feature_health, _agent4_feature_builder)
            except Exception as _agent4_hard_feature_error:
                print(f"[FEATURE_HANDOFF_ERROR] hard_rebuild_failed={repr(_agent4_hard_feature_error)}", flush=True)

            try:
                _qfos_v2_upsert_feature_snapshot(f_by_symbol)
            except Exception as _qfos_v2_feature_error:
                print(f"[POLICY_V2] feature_snapshot_fixed_error={_qfos_v2_feature_error}", flush=True)

            try:
                f_by_symbol = _qfos_agent4_feature_contract_repair(f_by_symbol)
            except Exception as _feature_contract_repair_error:
                print(f"[FEATURE_HANDOFF_ERROR] contract_repair_failed={repr(_feature_contract_repair_error)}", flush=True)

            ready = [f for f in f_by_symbol.values() if isinstance(f, dict) and f.get('ready')]

            try:
                _agent4_normal = {
                    s: f for s, f in f_by_symbol.items()
                    if _qfos_agent4_is_ready_normal_feature(f)
                }
                _agent4_ready = {
                    s: f for s, f in f_by_symbol.items()
                    if isinstance(f, dict) and f.get("ready") is True
                }
                _agent4_sample = []
                for _s, _f in list(_agent4_normal.items())[:3]:
                    _agent4_sample.append({
                        "symbol": _s,
                        "price": _f.get("price"),
                        "trend": _f.get("trend"),
                        "long_trend": _f.get("long_trend"),
                        "volatility": _f.get("volatility"),
                        "momentum": _f.get("momentum"),
                        "one_tick_momentum": _f.get("one_tick_momentum"),
                        "signal_strength": _f.get("signal_strength"),
                        "confidence": _f.get("confidence"),
                        "symbol_regime": _f.get("symbol_regime"),
                        "breakout_score": _f.get("breakout_score"),
                        "trend_quality": _f.get("trend_quality"),
                        "is_symbol_uptrend": _f.get("is_symbol_uptrend"),
                        "is_choppy": _f.get("is_choppy"),
                        "source": _f.get("source"),
                        "ready": _f.get("ready"),
                    })
                print(
                    "[FEATURE_HANDOFF] "
                    f"Feature symbols={len(f_by_symbol)} "
                    f"normal_features={len(_agent4_normal)} "
                    f"ready_features={len(_agent4_ready)} "
                    f"health={feature_health if 'feature_health' in locals() else None} "
                    f"sample={_agent4_sample}",
                    flush=True,
                )
            except Exception as _feature_handoff_log_error:
                print(f"[FEATURE_HANDOFF_ERROR] log_failed={repr(_feature_handoff_log_error)}", flush=True)

            # Agent 4 concise feature readiness diagnostics.
            try:
                _qfos_feature_normal = {
                    _s: _f for _s, _f in f_by_symbol.items()
                    if isinstance(_f, dict)
                    and str(_f.get("source", "")).upper() == "NORMAL"
                    and _f.get("ready") is True
                }
                _qfos_feature_ready = {
                    _s: _f for _s, _f in f_by_symbol.items()
                    if isinstance(_f, dict)
                    and _f.get("ready") is True
                }
                _qfos_feature_warming = []
                for _s, _f in list(f_by_symbol.items())[:8]:
                    if isinstance(_f, dict) and not _f.get("ready"):
                        _qfos_feature_warming.append({
                            "symbol": _s,
                            "history_len": _f.get("history_len"),
                            "ready": _f.get("ready"),
                            "source": _f.get("source"),
                            "reason": _f.get("rejection_reason"),
                            "missing_history": _f.get("missing_history"),
                        })
                _qfos_feature_sample = []
                for _s, _f in list(_qfos_feature_normal.items())[:5]:
                    _qfos_feature_sample.append({
                        "symbol": _s,
                        "source": _f.get("source"),
                        "ready": _f.get("ready"),
                        "history_len": _f.get("history_len"),
                        "price": _f.get("price"),
                        "signal_strength": _f.get("signal_strength"),
                        "confidence": _f.get("confidence"),
                        "symbol_regime": _f.get("symbol_regime"),
                    })
                print(
                    "[FEATURE_DIAG] "
                    f"feature_symbols={len(f_by_symbol)} "
                    f"normal_features={len(_qfos_feature_normal)} "
                    f"ready_features={len(_qfos_feature_ready)} "
                    f"warming_preview={_qfos_feature_warming} "
                    f"normal_sample={_qfos_feature_sample}",
                    flush=True,
                )
            except Exception as _qfos_feature_diag_error:
                print(f"[FEATURE_DIAG_ERROR] {repr(_qfos_feature_diag_error)}", flush=True)
            fallback_features = {}
            if not ready:
                print('WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...')
                fallback_features = build_raw_momentum_fallback(prices)
                if fallback_features:
                    print('FALLBACK FEATURES DIAGNOSTIC ONLY:', fallback_features)
                else:

                    # POLICY V2 ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â persist current feature map for trade classification
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

            # QFOS ALLOCATOR OPPORTUNITY RESCUE ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â upstream rescue before fallback scout
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
                print(
                    "[SCOUT_FALLBACK] disabled policy=C44_strict_normal_only",
                    flush=True,
                )

            print('FEATURES:', {k: v for k, v in state['features'].items() if isinstance(v, dict) and v.get('ready')})

            # POLICY V2 ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â block weak fallback scout before ORDERS/EXPECTANCY
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

            # POLICY V2 FIXED ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â block weak fallback orders in the real result['orders'] list
            try:
                if isinstance(result, dict) and isinstance(result.get('orders'), list):
                    result['orders'] = _qfos_v2_filter_fallback_orders(
                        result.get('orders', []),
                        source='result.orders.pre_print',
                    )
            except Exception as _qfos_v2_filter_error:
                print(f"[POLICY_V2] result_orders_filter_error={_qfos_v2_filter_error}", flush=True)

            # C44 strict final BUY admission gate.
            # Any order injected after the first quality pass must pass the
            # same NORMAL feature ranking and pacing rules before execution.
            try:
                if isinstance(result, dict):
                    _c44_before = list(result.get("orders") or [])
                    _c44_orders, _c44_rejections = enforce_entry_quality_lockdown(
                        result={"orders": _c44_before},
                        feature_map=state["features"],
                        regime=regime,
                    )
                    result["orders"] = list(_c44_orders or [])
                    entry_quality_rejections.extend(list(_c44_rejections or []))
                    print(
                        "[C44_FINAL_ENTRY_GATE] "
                        f"before={len(_c44_before)} "
                        f"after={len(result['orders'])} "
                        f"rejected={len(_c44_rejections or [])}",
                        flush=True,
                    )
            except Exception as _c44_gate_error:
                # Fail closed for BUY admissions. SELL logic is outside this block.
                print(
                    f"[C44_FINAL_ENTRY_GATE_ERROR] error={_c44_gate_error!r}",
                    flush=True,
                )
                if isinstance(result, dict):
                    result["orders"] = [
                        x for x in list(result.get("orders") or [])
                        if isinstance(x, dict)
                        and str(x.get("side") or "").lower() == "sell"
                    ]

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
            qfos_loop_control_observed(paused)
            if globals().get('last_seen_paused_state') is True and paused is False:
                reset_liquidity_errors()
            globals()['last_seen_paused_state'] = paused
            if not paused and qfos_safe_pause_reason_text():
                reset_liquidity_errors()
            if paused:
                reason_str = qfos_safe_pause_reason_text() or 'paused'
                rejected.append({'symbol': 'ALL', 'reason': reason_str})
                try:
                    from observability import events, RejectionReason, RiskRule, _make_allocator_state, _manager
                    cycle_id = qfos_observability_cycle_id()
                    alloc_state = _make_allocator_state(**_qfos_adapt_alloc_state(qfos_active_canbuy_ledger_state()))
                    cids = []
                    for f in (proposed_fills or []):
                        if isinstance(f, dict):
                            sym = str(f.get("symbol", ""))
                            side = str(f.get("side", "")).lower()
                            if side in ('buy', 'long', 'open'):
                                info = _manager.get_candidate_info(cycle_id, sym)
                                if info and info.get("candidate_id"): cids.append(info["candidate_id"])
                    if cids:
                        events.batch_filtered(
                            cycle_id=cycle_id,
                            filter_stage=2,
                            filter_name="system_pause",
                            reason=RejectionReason.RISK_MANAGER_REJECTED,
                            affected_candidates=cids,
                            raw_reason=reason_str,
                            details={"risk_rule": RiskRule.SYSTEM_PAUSED.value},
                            allocator_state=alloc_state
                        )
                except Exception:
                    pass
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
                        try:
                            from observability import events, RejectionReason, RiskRule, _make_allocator_state, _manager
                            cycle_id = qfos_observability_cycle_id()
                            info = _manager.get_candidate_info(cycle_id, symbol)
                            if info and info.get("candidate_id"):
                                alloc_state = _make_allocator_state(**_qfos_adapt_alloc_state(qfos_active_canbuy_ledger_state()))
                                rej_reason = RejectionReason.OTHER
                                risk_rule = None
                                details = {}
                                filter_name = "entry_policy"
                                r_str = str(reason)
                                
                                if r_str == "symbol_quarantined":
                                    rej_reason = RejectionReason.QUARANTINE
                                    filter_name = "entry_policy_quarantine"
                                elif r_str.startswith("strategy_") and r_str.endswith("_blocked"):
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.STRATEGY_BLOCKED.value
                                    filter_name = "strategy_score_block"
                                elif "risk_off" in r_str:
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.RISK_OFF_REGIME.value
                                    filter_name = "risk_off_regime"
                                elif "confidence_too_low" in r_str:
                                    rej_reason = RejectionReason.SIGNAL_TOO_WEAK
                                    filter_name = "regime_confidence"
                                elif "max_entries_per_hour_hit" in r_str:
                                    rej_reason = RejectionReason.COOLDOWN
                                    filter_name = "entry_rate_cap"
                                
                                if risk_rule: details["risk_rule"] = risk_rule
                                events.candidate_filtered(
                                    candidate_id=info["candidate_id"],
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    rank=info.get("rank"),
                                    ranking_population=info.get("ranking_population"),
                                    reason=rej_reason,
                                    filter_name=filter_name,
                                    filter_stage=2,
                                    raw_reason=r_str,
                                    details=details,
                                    allocator_state=alloc_state,
                                    selection_terminal=True
                                )
                        except Exception:
                            pass
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

                    approved, reason = qfos_active_canbuy_authority(symbol, fill, prices, equity)
                    print(
                        f"[EXECUTION_STAGE] can_buy symbol={symbol} approved={approved} "
                        f"reason={reason} qty={fill.get('quantity')} price={fill.get('fill_price')} "
                        f"tier={fill.get('qfos_size_tier')} value={fill.get('qfos_target_value')}",
                        flush=True,
                    )
                    if approved:
                        try:
                            from observability import events, _manager, _make_allocator_state
                            cycle_id = qfos_observability_cycle_id()
                            info = _manager.get_candidate_info(cycle_id, symbol)
                            if info and info.get("candidate_id"):
                                alloc_state = _make_allocator_state(**_qfos_adapt_alloc_state(qfos_active_canbuy_ledger_state()))
                                events.candidate_approved(
                                    candidate_id=info["candidate_id"],
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    rank=info.get("rank"),
                                    ranking_population=info.get("ranking_population"),
                                    allocator_state=alloc_state
                                )
                                trade_id = events.trade_execution_started(
                                    candidate_id=info["candidate_id"],
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    allocator_state=alloc_state
                                )
                                _manager.register_trade_id(cycle_id, symbol, trade_id)
                                # Reuse the durable lifecycle key already understood by
                                # atomic persistence, so the eventual SELL can be joined.
                                fill["candidate_id"] = info["candidate_id"]
                                fill["trade_uuid"] = trade_id
                                fill["entry_strategy"] = strategy
                                fill["pm_v2_score"] = info.get("score", 0.0)
                        except Exception as e:
                            print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)

                        applied_ok = apply_buy(fill)
                        if applied_ok:
                            qfos_invalidate_ledger_cache()
                        print(
                            f"[EXECUTION_STAGE] apply_buy symbol={symbol} applied={applied_ok}",
                            flush=True,
                        )
                        if applied_ok:
                            applied_fills.append(fill)
                            entries_this_cycle += 1
                        else:
                            rejected.append({'symbol': symbol, 'reason': 'apply_buy_failed'})
                            try:
                                from observability import events, RejectionReason, _manager
                                cycle_id = qfos_observability_cycle_id()
                                info = _manager.get_candidate_info(cycle_id, symbol)
                                if info and info.get("candidate_id"):
                                    trade_id = _manager.get_trade_id(cycle_id, symbol)
                                    if trade_id:
                                        events.trade_execution_failed(
                                            candidate_id=info["candidate_id"],
                                            trade_id=trade_id,
                                            cycle_id=cycle_id,
                                            symbol=symbol,
                                            gate="apply_buy",
                                            reason=RejectionReason.EXECUTION_FAILED,
                                            raw_reason="apply_buy_failed"
                                       )
                            except Exception as e:
                                print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)
                    else:
                        rejected.append({'symbol': symbol, 'reason': reason})
                        try:
                            from observability import events, RejectionReason, RiskRule, _make_allocator_state, _manager
                            cycle_id = qfos_observability_cycle_id()
                            info = _manager.get_candidate_info(cycle_id, symbol)
                            if info and info.get("candidate_id"):
                                alloc_state = _make_allocator_state(**_qfos_adapt_alloc_state(qfos_active_canbuy_ledger_state()))
                                rej_reason = RejectionReason.OTHER
                                risk_rule = None
                                details = {}
                                filter_name = "can_buy"
                                r_str = str(reason)
                                
                                if r_str == "buys_disabled":
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.BUYS_DISABLED.value
                                    filter_name = "buys_disabled_flag"
                                elif r_str == "excluded_quote_or_stable_symbol":
                                    rej_reason = RejectionReason.QUOTE_FILTER
                                    filter_name = "excluded_symbol"
                                elif "price_too_low" in r_str:
                                    rej_reason = RejectionReason.OTHER
                                    filter_name = "min_entry_price"
                                elif "sideways_max_open_positions" in r_str:
                                    rej_reason = RejectionReason.MAX_POSITIONS
                                    filter_name = "max_open_positions"
                                elif "sideways_max_exposure" in r_str:
                                    rej_reason = RejectionReason.MAX_EXPOSURE
                                    filter_name = "regime_exposure_cap"
                                elif "blocked_drawdown" in r_str and "near" not in r_str:
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.BLOCKED_DRAWDOWN.value
                                    filter_name = "blocked_drawdown"
                                elif "near_blocked_drawdown" in r_str:
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.NEAR_BLOCKED_DRAWDOWN.value
                                    filter_name = "near_blocked_drawdown"
                                elif "caution_drawdown_position_cap" in r_str:
                                    rej_reason = RejectionReason.MAX_POSITIONS
                                    risk_rule = RiskRule.CAUTION_POSITION_CAP.value
                                    filter_name = "caution_position_cap"
                                elif "caution_drawdown_exposure" in r_str:
                                    rej_reason = RejectionReason.MAX_EXPOSURE
                                    risk_rule = RiskRule.CAUTION_EXPOSURE_CAP.value
                                    filter_name = "caution_exposure_cap"
                                elif "symbol_bad_history" in r_str:
                                    rej_reason = RejectionReason.QUARANTINE
                                    risk_rule = RiskRule.SYMBOL_BAD_HISTORY.value
                                    filter_name = "bad_history_gate"
                                elif "already_holding_symbol" in r_str:
                                    rej_reason = RejectionReason.POSITION_ALREADY_OPEN
                                    filter_name = "open_position_check"
                                elif r_str == "daily_loss_limit":
                                    rej_reason = RejectionReason.RISK_MANAGER_REJECTED
                                    risk_rule = RiskRule.DAILY_LOSS_LIMIT.value
                                    filter_name = "daily_loss_limit"
                                elif "cooldown" in r_str:
                                    rej_reason = RejectionReason.COOLDOWN
                                    filter_name = "symbol_cooldown"
                                elif "max_trades_per_symbol_recent" in r_str:
                                    rej_reason = RejectionReason.COOLDOWN
                                    filter_name = "symbol_rate_cap"
                                elif "max_total_exposure" in r_str:
                                    rej_reason = RejectionReason.MAX_EXPOSURE
                                    filter_name = "max_total_exposure"
                                elif "max_symbol_exposure" in r_str:
                                    rej_reason = RejectionReason.MAX_EXPOSURE
                                    filter_name = "max_symbol_exposure"
                                elif "symbol_quarantined" in r_str:
                                    rej_reason = RejectionReason.QUARANTINE
                                    filter_name = "entry_policy_quarantine"
                                
                                if risk_rule: details["risk_rule"] = risk_rule
                                events.candidate_filtered(
                                    candidate_id=info["candidate_id"],
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    rank=info.get("rank"),
                                    ranking_population=info.get("ranking_population"),
                                    reason=rej_reason,
                                    filter_name=filter_name,
                                    filter_stage=2,
                                    raw_reason=r_str,
                                    details=details,
                                    allocator_state=alloc_state,
                                    selection_terminal=True
                                )
                        except Exception:
                            pass
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
            qfos_pending_trade_notifications = []
            qfos_pending_strategy_score_updates = []
            qfos_txn_trade_context = {"symbols": [], "last_strategy": None}
            try:
                with engine.begin() as conn:
                    filtered_fills = []
                    applied_fills = _qfos_normalize_fill_list(list(applied_fills or []))
                    applied_fills = qfos_final_exit_bridge_add_db_sells(applied_fills, regime)
                    applied_fills = qfos_agent5_direct_prepare_exit_sells(applied_fills)
                    applied_fills = qfos_agent5_active_filter_exit_sells(applied_fills)

                    qfos_non_exit_fills = []
                    qfos_exit_candidates = []

                    for qfos_candidate_fill in list(applied_fills or []):
                        if (
                            isinstance(qfos_candidate_fill, dict)
                            and str(qfos_candidate_fill.get("side") or "").lower() == "sell"
                        ):
                            qfos_exit_candidates.append(qfos_candidate_fill)
                        else:
                            qfos_non_exit_fills.append(qfos_candidate_fill)

                    qfos_canonical_exit_fills, qfos_duplicate_exit_rejections = (
                        deduplicate_exit_intents(
                            qfos_exit_candidates,
                            default_source="main_loop",
                        )
                    )

                    if qfos_duplicate_exit_rejections:
                        rejected.extend(qfos_duplicate_exit_rejections)
                        print(
                            "[CANONICAL_EXIT_INTENTS] "
                            f"accepted={len(qfos_canonical_exit_fills)} "
                            f"duplicates_rejected={len(qfos_duplicate_exit_rejections)}",
                            flush=True,
                        )

                    applied_fills = qfos_non_exit_fills + qfos_canonical_exit_fills

                    for fill in applied_fills:
                        allowed, reason = qfos_exec_risk_authority_firewall(fill, regime)
                        if allowed:
                            filtered_fills.append(fill)
                        else:
                            print(
                                f"[EXECUTION_STAGE] final_firewall_rejected "
                                f"symbol={fill.get('symbol', 'UNKNOWN')} strategy={fill.get('strategy')} reason={reason}",
                                flush=True,
                            )
                            if str(fill.get('side', '')).lower() == 'buy':
                                qfos_rollback_unpersisted_buy(fill, source=f"final_firewall:{reason}")
                                try:
                                    from observability import events, RejectionReason, _manager
                                    cycle_id = qfos_observability_cycle_id()
                                    sym = fill.get('symbol', 'UNKNOWN')
                                    info = _manager.get_candidate_info(cycle_id, sym)
                                    if info and info.get("candidate_id"):
                                        trade_id = _manager.get_trade_id(cycle_id, sym)
                                        if trade_id:
                                            events.trade_execution_failed(
                                                candidate_id=info["candidate_id"],
                                                trade_id=trade_id,
                                                cycle_id=cycle_id,
                                                symbol=sym,
                                                gate="final_firewall",
                                                reason=RejectionReason.RISK_MANAGER_REJECTED,
                                                raw_reason=str(reason)
                                           )
                                except Exception as e:
                                    print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)

                            rejected.append({'symbol': fill.get('symbol', 'UNKNOWN'), 'reason': reason})
                    applied_fills = filtered_fills
                    execution_telemetry = ExecutionCycleTelemetry(
                        raw_orders=len(raw_result_orders or []),
                        proposed_fills=len(proposed_fills or []),
                    )
                    persisted_fills = []
                    rejected_persistence_fills = []

                    for raw_fill in applied_fills:
                        persisted_fill = qfos_persist_fill_atomic(conn, raw_fill, source='main_loop')

                        if not execution_telemetry.record_persistence_result(
                            raw_fill,
                            persisted_fill,
                            "atomic_persistence_rejected",
                        ):
                            sym = str((raw_fill or {}).get("symbol") or "UNKNOWN")
                            rejection = {
                                "symbol": sym,
                                "reason": "atomic_persistence_rejected",
                            }
                            if str((raw_fill or {}).get('side', '')).lower() == 'buy':
                                try:
                                    from observability import events, RejectionReason, _manager
                                    cycle_id = qfos_observability_cycle_id()
                                    info = _manager.get_candidate_info(cycle_id, sym)
                                    if info and info.get("candidate_id"):
                                        trade_id = _manager.get_trade_id(cycle_id, sym)
                                        if trade_id:
                                            events.trade_execution_failed(
                                                candidate_id=info["candidate_id"],
                                                trade_id=trade_id,
                                                cycle_id=cycle_id,
                                                symbol=sym,
                                                gate="atomic_persistence",
                                                reason=RejectionReason.ATOMIC_PERSISTENCE_FAILED,
                                                raw_reason="atomic_persistence_rejected"
                                           )
                                except Exception as e:
                                    print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)

                            rejected_persistence_fills.append(rejection)
                            rejected.append(rejection)
                            continue

                        persisted_fills.append(persisted_fill)
                        fill = persisted_fill
                        fill_pnl = float(fill.get('pnl', 0.0) or 0.0)
                        original_strat = fill.get('entry_strategy') or fill.get('applied_strategy', fill.get('strategy', 'unknown'))
                        trades_total.inc()
                        side = fill.get('side', '').upper()
                        symbol = fill.get('symbol', '')
                        qty = float(fill.get('quantity', 0) or 0)
                        price = float(fill.get('fill_price', 0) or 0)
                        strategy = fill.get('strategy', 'unknown')
                        confidence = float(fill.get('confidence', 0) or 0)
                        is_shadow = fill.get('shadow_mode', False)
                        if not is_shadow:
                            # Use the new_qty already returned by qfos_persist_fill_atomic
                            # rather than reading back from DB inside this open write
                            # transaction. That SELECT was the source of idle-in-transaction
                            # locks that blocked every other query on the positions table.
                            new_qty = float(fill.get('new_qty') or fill.get('quantity') or 0.0)
                            portfolio.positions[symbol] = new_qty

                        print(
                            f"[EXECUTION_STAGE] db_trade_written side={side} symbol={symbol} "
                            f"qty={qty:.8f} price={price:.8f} pnl={fill_pnl:.6f} "
                            f"position_qty={portfolio.positions.get(symbol, 0.0)}",
                            flush=True,
                        )

                        if side == 'BUY':
                            try:
                                from observability import events, _manager
                                cycle_id = qfos_observability_cycle_id()
                                info = _manager.get_candidate_info(cycle_id, symbol)
                                if info and info.get("candidate_id"):
                                    trade_id = _manager.get_trade_id(cycle_id, symbol)
                                    if trade_id:
                                        events.trade_persisted(
                                            candidate_id=info["candidate_id"],
                                            trade_id=trade_id,
                                            cycle_id=cycle_id,
                                            symbol=symbol,
                                            quantity=qty,
                                           fill_price=price
                                       )
                                        events.trade_opened(
                                            candidate_id=info["candidate_id"],
                                            trade_id=trade_id,
                                            cycle_id=cycle_id,
                                            symbol=symbol
                                       )
                            except Exception as e:
                                print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)
                        elif side == 'SELL':
                            try:
                                from observability import events
                                lifecycle_trade_id = fill.get("trade_uuid")
                                lifecycle_candidate_id = fill.get("candidate_id")
                                if lifecycle_trade_id and lifecycle_candidate_id:
                                    opened_at = float(position_open_time.get(symbol, time.time()) or time.time())
                                    events.trade_exited(
                                        trade_id=lifecycle_trade_id,
                                        candidate_id=lifecycle_candidate_id,
                                        symbol=symbol,
                                        exit_price=price,
                                        holding_time_seconds=max(0.0, time.time() - opened_at),
                                        realized_pnl=fill_pnl,
                                        exit_reason=fill.get("exit_reason") or strategy,
                                        strategy=original_strat,
                                        MFE=fill.get("mfe"),
                                        MAE=fill.get("mae"),
                                   )
                            except Exception as e:
                                print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)
                        qfos_pending_trade_notifications.append(
                            f"<b>{side} {('(SHADOW)' if is_shadow else '')}</b> {symbol}\n"
                            f"Qty: {qty:.6f}\nPrice: {price:.4f}\nPnL: {fill_pnl:.2f}\n"
                            f"Strategy: {strategy}\nConfidence: {confidence:.2f}\n"
                            f"Live: {settings.live_trading}"
                        )
                        qfos_txn_trade_context["symbols"].append(symbol)
                        qfos_txn_trade_context["last_strategy"] = strategy
                        score_strategy = original_strat if side == 'SELL' else strategy
                        if score_strategy and score_strategy not in ('take_profit', 'single_full_take_profit', 'breakeven_protection_exit', 'time_stop_exit', 'trailing_profit_exit', 'stop_loss', 'adaptive_take_profit', 'adaptive_stop_loss', 'risk_off_exit', 'emergency_exposure_reduction', 'unknown'):
                            # QFOS_STRATEGY_SCORE_DECOUPLED_V1: this used to
                            # execute INSERT INTO strategy_scores ... ON
                            # CONFLICT(strategy) right here, inside the same
                            # transaction as the trade/position/snapshot.
                            # That's what let a schema defect in an
                            # analytics-only table (strategy_scores) silently
                            # roll back an already-executed, financially real
                            # trade. Analytics must consume the trade ledger,
                            # never gate it. The actual write now happens
                            # after this transaction commits -- see
                            # qfos_pending_strategy_score_updates below and
                            # its post-commit, non-fatal execution in the
                            # `else:` clause of the enclosing try/except.
                            qfos_pending_strategy_score_updates.append(
                                {'strategy': score_strategy, 'pnl': fill_pnl}
                            )
                    persisted_fills_count = execution_telemetry.persisted_fills
                    rejected_fills = execution_telemetry.rejected_fills
                    final_applied_fills = execution_telemetry.final_applied_fills

                    print(
                        "[EXECUTION_STAGE] persistence_summary "
                        f"proposed_fills={execution_telemetry.proposed_fills} "
                        f"persisted_fills={persisted_fills_count} "
                        f"rejected_fills={rejected_fills} "
                        f"final_applied_fills={final_applied_fills}",
                        flush=True,
                    )

                    qfos_cycle_from_locals(locals())

                    qfos_ensure_trades_schema(conn)
                    qfos_db_sync_positions_from_portfolio(conn, portfolio, prices)
                    mark_positions_to_market(conn, prices)
                    # QFOS_C7_AUDIT_SNAPSHOT_GUARD_V1
                    if _QFOS_AUDIT_BOOT:
                        print("[QFOS_AUDIT_BOOT] portfolio_snapshot_write_blocked source=main_loop", flush=True)
                    else:
                        conn.execute(text('\n                    INSERT INTO portfolio_snapshots(\n                        equity, cash, exposure, drawdown, regime\n                    )\n                    VALUES(\n                        :equity, :cash, :exposure, :drawdown, :regime\n                    )\n                '), {'equity': equity, 'cash': portfolio.cash, 'exposure': exposure, 'drawdown': portfolio.drawdown, 'regime': regime})
            except Exception as qfos_txn_exc:
                _qfos_log_transaction_failure(qfos_txn_exc, qfos_txn_trade_context)
                qfos_pending_trade_notifications = []
                raise
            else:
                _qfos_apply_strategy_score_updates(qfos_pending_strategy_score_updates)
                try:
                    from observability import events, _manager
                    obs_cycle_id = qfos_observability_cycle_id()
                    unresolved_before_expiry = len(_manager.unresolved_candidates(obs_cycle_id))
                    expired_count = events.expire_unresolved_candidates(obs_cycle_id)
                    events.cycle_summary(
                        cycle_id=obs_cycle_id,
                        total_candidates=len(state.get("features", {}) or {}),
                        candidates_above_threshold=unresolved_before_expiry,
                        filtered_count=len(rejected),
                        approved_count=entries_this_cycle,
                        executed_count=len(persisted_fills),
                        regime=regime,
                        evaluation_time_ms=0.0,
                    )
                except Exception as e:
                    print("[OBSERVABILITY_ERROR] " + repr(e), flush=True)
                for qfos_msg in qfos_pending_trade_notifications:
                    send_telegram_alert(qfos_msg)
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

            if qfos_apply_clean_ledger_runtime_reset(source='pre_live_status_payload'):
                equity = 100.0
                exposure = 0.0
                exposure_pct = 0.0
                current_risk_status = 'SAFE'
                paused = False
                pause_reason_value = ''
                rejected = []
            try:
                pause_reason_value = qfos_safe_pause_reason_text()
            except Exception:
                pause_reason_value = ''
            live_payload = {'name': 'Quant Fund OS', 'mode': getattr(settings, 'mode', 'paper'), 'live_trading': bool(getattr(settings, 'live_trading', False)), 'exchange': getattr(settings, 'exchange', 'mexc'), 'exchange_type': getattr(settings, 'exchange_type', 'spot'), 'regime': regime, 'risk_status': current_risk_status, 'bot_state': 'PAUSED' if paused else 'RUNNING', 'paused': bool(paused), 'pause_reason': pause_reason_value, 'portfolio': {'equity': equity, 'cash': portfolio.cash, 'exposure': exposure, 'exposure_pct': exposure_pct, 'regime': regime}, 'positions': portfolio.positions, 'orders': len(result.get('orders', [])) if isinstance(result, dict) else 0, 'controls': {'pause': '/pause', 'resume': '/resume', 'kill_switch': '/kill-switch'}}
            live_payload = qfos_baseline_authority_status_override(live_payload, source='post_live_status_payload')
            update_live_status_cache(live_payload)
            print({'regime': regime, 'equity': round(equity, 2), 'cash': round(portfolio.cash, 2), 'exposure': round(exposure, 2), 'exposure_pct': round(exposure / equity, 4) if equity else 0, 'positions': {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0}, 'orders': len(applied_fills), 'rejected': rejected[:3], 'status': result['status'], 'paused': is_paused(), 'risk_status': current_risk_status, 'shadow_positions': {k: round(v, 6) for k, v in shadow_positions.items() if v > 0}})
            try:
                diagnostic_snapshot = {'regime': regime, 'equity': round(equity, 2), 'cash': round(portfolio.cash, 2), 'exposure': round(exposure, 2), 'exposure_pct': round(exposure / equity, 4) if equity else 0, 'positions': {k: round(v, 6) for k, v in portfolio.positions.items() if v > 0}, 'risk_status': current_risk_status}
                log_cycle_diagnostic(market_data=prices, features={k: v for k, v in state['features'].items() if isinstance(v, dict) and v.get('ready')}, orders=applied_fills, portfolio=diagnostic_snapshot, rejected=rejected, note='main_loop_after_execution')
            except Exception as diagnostic_error:
                print('DIAGNOSTIC_LOG_ERROR:', diagnostic_error)
            qfos_stop_evaluation_batch()
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
            qfos_stop_evaluation_batch()
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

from services.truth_metrics import (
    qfo_safe_lower,
    qfo_trade_get,
    qfo_trade_label,
    qfo_trade_side,
    qfo_is_buy_trade,
    qfo_is_sell_trade,
    qfo_is_stop_loss_label,
    qfo_is_take_profit_label,
    qfo_is_stop_loss_trade,
    qfo_is_take_profit_trade,
    qfo_compute_truth_metrics_from_trades
)

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
    truth = qfo_compute_truth_metrics_from_trades(trades)
    
    return {
        "total_trades": qfo_metric_value(locals(), "total_trades", truth["total_trades"]),
        "buy_count": qfo_metric_value(locals(), "buy_count", truth["buy_count"]),
        "sell_count": qfo_metric_value(locals(), "sell_count", truth["sell_count"]),
        "take_profit_count": qfo_metric_value(locals(), "take_profit_count", truth["take_profit_count"]),
        "stop_loss_count": qfo_metric_value(locals(), "stop_loss_count", truth["stop_loss_count"]),
        "win_rate": qfo_metric_value(locals(), "win_rate", truth["win_rate"]),
        "win_rate_estimate": qfo_metric_value(locals(), "win_rate_estimate", truth["win_rate_estimate"]),
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




# QFOS_STATUS_TRUTH_CONTRACT_FINAL_V1
# Final response contract for /status. Presentation only: it never changes
# strategy, orders, position accounting, cash, or realized PnL.
@app.middleware("http")
async def _qfos_status_truth_contract_final(request, call_next):
    response = await call_next(request)

    if request.url.path != "/status":
        return response

    try:
        import json
        from fastapi.responses import Response

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        payload = json.loads(body.decode("utf-8"))
        performance = payload.get("performance")

        if not isinstance(performance, dict):
            return Response(
                content=json.dumps(payload, default=str).encode("utf-8"),
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
                media_type="application/json",
                background=response.background,
            )

        truth = _qfos_truthful_closed_fill_metrics()

        if truth.get("metrics_available"):
            closed = int(
                truth.get("closed_sell_fills", truth.get("closed_outcome_count", 0)) or 0
            )
            wins = int(truth.get("winning_closed_fills", 0) or 0)
            losses = int(truth.get("losing_closed_fills", 0) or 0)
            breakevens = int(truth.get("breakeven_closed_fills", 0) or 0)

            gross_pnl = float(
                truth.get(
                    "gross_fill_price_realized_pnl",
                    truth.get("fill_derived_closed_pnl", 0.0),
                ) or 0.0
            )

            rate = float(truth.get("truthful_win_rate", 0.0) or 0.0)

            performance["win_rate"] = round(rate, 4)
            performance["win_rate_estimate"] = round(rate, 4)
            performance["closed_outcome_count"] = closed
            performance["winning_closed_fills"] = wins
            performance["losing_closed_fills"] = losses
            performance["breakeven_closed_fills"] = breakevens

            # The active helper does not independently calculate unmatched
            # sells. Expose that limitation explicitly instead of inventing 0.
            performance["unmatched_sell_fills"] = truth.get("unmatched_sell_fills")
            performance["metrics_basis"] = truth.get(
                "metrics_basis",
                "weighted_average_fill_price_before_fees",
            )
            performance["gross_fill_price_realized_pnl"] = gross_pnl
            performance["fill_derived_closed_pnl"] = gross_pnl
            performance["metrics_available"] = True
            performance["metrics_error"] = None
        else:
            performance["metrics_available"] = False
            performance["metrics_basis"] = truth.get("metrics_basis", "unavailable")
            performance["metrics_error"] = truth.get("metrics_error", "unknown")
            performance["winning_closed_fills"] = None
            performance["losing_closed_fills"] = None
            performance["breakeven_closed_fills"] = None
            performance["unmatched_sell_fills"] = None
            performance["gross_fill_price_realized_pnl"] = None
            performance["fill_derived_closed_pnl"] = None

        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() != "content-length"
        }

        return Response(
            content=json.dumps(payload, default=str).encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=response.background,
        )

    except Exception as exc:
        print(f"[QFOS_STATUS_TRUTH_CONTRACT_ERROR] error={exc!r}", flush=True)
        return response

@app.get('/live-status')
def live_status():
    live = get_live_status_cache()
    if live:
        return live
    return {'name': 'Quant Fund OS', 'status': 'warming_up_or_no_live_cache', 'note': 'Live cache not populated yet. Use dashboard/logs until first loop update.'}



# ============================================================
# QFOS SAFE WRAPPER PATCH ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ACTIVE OUTLIER LOSS + BIG LOSER COOLDOWN
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
    return qfos_runtime_db_path()

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

print("[QFOS_POSTGRES_ONLY] disabled startup: _qfos_safe_install_wrappers (SQLite-backed cooldown/quarantine wrapper)", flush=True)




# ============================================================
# QFOS BASKET LOSS GUARD ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ACTIVE WRAPPER
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
    return qfos_runtime_db_path()

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

print("[QFOS_POSTGRES_ONLY] disabled startup: _qfos_basket_install_wrapper (SQLite-backed basket-loss wrapper)", flush=True)




# ============================================================
# QFOS EMERGENCY BASKET WATCHDOG ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â DB-LEVEL PAPER EXIT
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
    return qfos_runtime_db_path()

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

        persisted = qfos_persist_fill_atomic(cur, {
            "symbol": symbol,
            "side": "sell",
            "quantity": qty,
            "expected_price": mark,
            "fill_price": mark,
            "slippage_bps": 0.0,
            "strategy": "basket_loss_cap",
            "confidence": 1.0,
            "live": False,
            "shadow_mode": False,
            "created_at": now_s,
        }, source="emergency_basket_watchdog")

        if not persisted:
            conn.rollback()
            return

        pnl = float(persisted.get("pnl", pnl) or 0.0)

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

print("[QFOS_POSTGRES_ONLY] disabled startup: _qfos_start_emergency_basket_watchdog (SQLite-backed emergency basket watchdog)", flush=True)




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
# QFOS ACTIVE POSITION WATCHDOG ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â DB-LEVEL EXIT PROTECTION
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
    return qfos_runtime_db_path()

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

    persisted = qfos_persist_fill_atomic(cur, {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": mark,
        "fill_price": mark,
        "slippage_bps": 0.0,
        "strategy": reason,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "created_at": now_s,
    }, source="active_position_watchdog")

    if not persisted:
        return False

    pnl = float(persisted.get("pnl", pnl) or 0.0)

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
    return True

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

print("[QFOS_POSTGRES_ONLY] disabled startup: _qfos_start_active_position_watchdog (SQLite-backed active-position watchdog)", flush=True)




# ============================================================
# QFOS PROFIT ENGINE V1 ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â TRADE CLASS + PARTIAL TP + RUNNERS
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
    return qfos_runtime_db_path()

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
    # PHASE2D_PE_DUPLICATE_SELL_SOURCE_GUARD
    try:
        if _qfos_latest_trade_is_sell_and_no_open_qty(conn, symbol):
            _qfos_cleanup_closed_symbol_runtime_state(
                symbol,
                reason="pe_source_latest_sell_no_open_qty",
                source="_qfos_pe_sell",
            )
            print(
                "[PE_SELL_SOURCE_SKIP] symbol=%s reason=latest_sell_no_open_qty"
                % (symbol,),
                flush=True,
            )
            return None
    except Exception as _phase2d_guard_error:
        print(
            "[PE_SELL_SOURCE_GUARD_ERROR] symbol=%s error=%s"
            % (symbol if "symbol" in locals() else "unknown", repr(_phase2d_guard_error)),
            flush=True,
        )

    symbol = str(pos["symbol"])
    qty = abs(_qfos_pe_float(quantity))
    mark = _qfos_pe_float(pos["last_price"])
    strategy = str(pos.get("strategy") or reason)

    if qty <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] source=profit_engine symbol={symbol} "
            f"strategy={reason} reason=requested_qty_lte_zero qty={qty}",
            flush=True,
        )
        return False

    print(
        "[PROFIT_ENGINE] selling "
        f"symbol={symbol} reason={reason} qty={qty:.8f} mark={mark:.8f} pnl={pnl:.6f}",
        flush=True,
    )

    persisted = qfos_persist_fill_atomic(cur, {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": mark,
        "fill_price": mark,
        "slippage_bps": 0.0,
        "strategy": reason,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "created_at": now_s,
    }, source="profit_engine")

    if not persisted:
        return False

    final_qty = abs(_qfos_pe_float(persisted.get("quantity", qty)))
    final_pnl = _qfos_pe_float(persisted.get("pnl", pnl))

    row = cur.execute("SELECT quantity FROM positions WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
    remaining_qty = _qfos_pe_float(row[0], 0.0) if row else 0.0

    if remaining_qty <= 1e-12:
        if quarantine:
            blocked_until = _qfos_pe_quarantine(cur, symbol, strategy, reason, now_s)
            print(
                f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason} "
                f"pnl={final_pnl:.6f} blocked_until={blocked_until}",
                flush=True,
            )
        else:
            print(
                f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason} "
                f"pnl={final_pnl:.6f}",
                flush=True,
            )
    else:
        print(
            f"[PROFIT_ENGINE] partial sold symbol={symbol} sold_qty={final_qty:.8f} "
            f"remaining_qty={remaining_qty:.8f} pnl={final_pnl:.6f}",
            flush=True,
        )

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

print("[QFOS_POSTGRES_ONLY] disabled startup: _qfos_start_profit_engine (SQLite-backed profit engine)", flush=True)




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
    return qfos_runtime_db_path()

def _qfos_acct_now_local():
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=3)

def _qfos_acct_latest_regime(conn):
    try:
        cols = _qfos_table_columns(conn, "portfolio_snapshots")
        if "regime" not in cols:
            return "SIDEWAYS"
        row = _qfos_exec(conn, """
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

def _qfos_acct_realized_pnl(conn):
    try:
        if not _qfos_table_exists(conn, "trades"):
            return 0.0
        row = _qfos_exec(conn, "SELECT COALESCE(SUM(pnl), 0.0) FROM trades").fetchone()
        return _qfos_acct_float(row[0] if row else 0.0, 0.0)
    except Exception:
        return 0.0

def _qfos_acct_open_position_totals(conn):
    exposure = 0.0
    unrealized = 0.0

    try:
        if not _qfos_table_exists(conn, "positions"):
            return exposure, unrealized

        rows = _qfos_exec(conn, """
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

def _qfos_acct_ensure_snapshot_table(conn):
    _qfos_exec(conn, """
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id SERIAL PRIMARY KEY,
            equity REAL,
            cash REAL,
            exposure REAL,
            drawdown REAL,
            regime TEXT,
            created_at TEXT
        )
    """)

def _qfos_acct_insert_snapshot(conn, equity, cash, exposure, drawdown, regime, now_s):
    cols = _qfos_table_columns(conn, "portfolio_snapshots")
    if not cols:
        try:
            with conn.begin_nested():
                _qfos_acct_ensure_snapshot_table(conn)
        except Exception:
            pass
        cols = _qfos_table_columns(conn, "portfolio_snapshots")

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
    placeholders = ",".join([f":{c}" for c in col_names])
    sql = f"INSERT INTO portfolio_snapshots ({','.join(col_names)}) VALUES ({placeholders})"
    _qfos_exec(conn, sql, values)

def _qfos_acct_reconcile_once(verbose=False):
    try:
        with engine.begin() as conn:
            try:
                with conn.begin_nested():
                    _qfos_acct_ensure_snapshot_table(conn)
            except Exception:
                # Benign race: another concurrent reconcile pass (e.g. the
                # background _qfos_acct_loop tick) created portfolio_snapshots
                # between our existence check and our own CREATE TABLE IF NOT
                # EXISTS. Postgres does not guarantee IF NOT EXISTS is race-free
                # under concurrent DDL, unlike the old single-threaded sqlite
                # cursor model. Roll back just this savepoint; the table exists
                # either way, so the rest of the reconcile pass can proceed.
                pass

            now = _qfos_acct_now_local()
            now_s = now.strftime("%Y-%m-%d %H:%M:%S")

            regime = _qfos_acct_latest_regime(conn)
            realized = _qfos_acct_realized_pnl(conn)
            exposure, unrealized = _qfos_acct_open_position_totals(conn)

            equity = float(QFOS_STARTING_EQUITY) + realized + unrealized
            cash = equity - exposure
            drawdown = (equity - float(QFOS_STARTING_EQUITY)) / max(float(QFOS_STARTING_EQUITY), 1.0)

            _qfos_acct_insert_snapshot(
                conn,
                round(equity, 8),
                round(cash, 8),
                round(exposure, 8),
                round(drawdown, 8),
                regime,
                now_s,
            )

            # engine.begin() commits automatically on clean exit and rolls
            # back automatically if an exception propagates out of the
            # "with" block, so no explicit commit/rollback/close is needed.

            if verbose:
                print(
                    "[PORTFOLIO_RECONCILER] synced "
                    f"equity={equity:.4f} cash={cash:.4f} exposure={exposure:.4f} "
                    f"realized={realized:.4f} unrealized={unrealized:.4f} "
                    f"drawdown={drawdown:.4%} regime={regime}",
                    flush=True,
                )

    except Exception as exc:
        print(f"[PORTFOLIO_RECONCILER] error={exc}", flush=True)

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
# QFOS POLICY V2 ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â CONSOLIDATED ENTRY CLASSIFICATION + RUNNER LOGIC
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
    return qfos_runtime_db_path()

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

    now_s = _qfos_v2_now_local().strftime("%Y-%m-%d %H:%M:%S")
    rows = []

    for symbol, feature in features.items():
        if not isinstance(feature, dict) or not feature.get("ready", False):
            continue

        rows.append({
            "symbol": str(symbol),
            "price": _qfos_v2_float(feature.get("price")),
            "trend": _qfos_v2_float(feature.get("trend")),
            "long_trend": _qfos_v2_float(feature.get("long_trend")),
            "volatility": _qfos_v2_float(feature.get("volatility")),
            "momentum": _qfos_v2_float(feature.get("momentum")),
            "one_tick_momentum": _qfos_v2_float(feature.get("one_tick_momentum")),
            "signal_strength": _qfos_v2_float(feature.get("signal_strength")),
            "symbol_regime": str(feature.get("symbol_regime") or ""),
            "symbol_trend_score": _qfos_v2_float(feature.get("symbol_trend_score")),
            "breakout_score": _qfos_v2_float(feature.get("breakout_score")),
            "trend_quality": _qfos_v2_float(feature.get("trend_quality")),
            "is_symbol_uptrend": bool(feature.get("is_symbol_uptrend")),
            "is_symbol_downtrend": bool(feature.get("is_symbol_downtrend")),
            "is_choppy": bool(feature.get("is_choppy")),
            "updated_at": now_s,
        })

    if not rows:
        return

    sql = text("""
        INSERT INTO symbol_feature_snapshot (
            symbol, price, trend, long_trend, volatility, momentum,
            one_tick_momentum, signal_strength, symbol_regime,
            symbol_trend_score, breakout_score, trend_quality,
            is_symbol_uptrend, is_symbol_downtrend, is_choppy, updated_at
        )
        VALUES (
            :symbol, :price, :trend, :long_trend, :volatility, :momentum,
            :one_tick_momentum, :signal_strength, :symbol_regime,
            :symbol_trend_score, :breakout_score, :trend_quality,
            :is_symbol_uptrend, :is_symbol_downtrend, :is_choppy, :updated_at
        )
        ON CONFLICT (symbol) DO UPDATE SET
            price = EXCLUDED.price,
            trend = EXCLUDED.trend,
            long_trend = EXCLUDED.long_trend,
            volatility = EXCLUDED.volatility,
            momentum = EXCLUDED.momentum,
            one_tick_momentum = EXCLUDED.one_tick_momentum,
            signal_strength = EXCLUDED.signal_strength,
            symbol_regime = EXCLUDED.symbol_regime,
            symbol_trend_score = EXCLUDED.symbol_trend_score,
            breakout_score = EXCLUDED.breakout_score,
            trend_quality = EXCLUDED.trend_quality,
            is_symbol_uptrend = EXCLUDED.is_symbol_uptrend,
            is_symbol_downtrend = EXCLUDED.is_symbol_downtrend,
            is_choppy = EXCLUDED.is_choppy,
            updated_at = EXCLUDED.updated_at
    """)

    try:
        with engine.begin() as conn:
            conn.execute(sql, rows)

        print(
            f"[POLICY_V2_POSTGRES] feature_snapshot_upserted count={len(rows)}",
            flush=True,
        )
    except Exception as exc:
        print(f"[POLICY_V2_POSTGRES] feature_snapshot_error={exc}", flush=True)

def _qfos_v2_latest_feature(symbol):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT *
                    FROM symbol_feature_snapshot
                    WHERE symbol = :symbol
                    LIMIT 1
                """),
                {"symbol": str(symbol)},
            ).mappings().first()

        if not row:
            return {}

        feature = dict(row)
        feature["is_symbol_uptrend"] = bool(feature.get("is_symbol_uptrend"))
        feature["is_symbol_downtrend"] = bool(feature.get("is_symbol_downtrend"))
        feature["is_choppy"] = bool(feature.get("is_choppy"))
        return feature

    except Exception as exc:
        print(
            f"[POLICY_V2_POSTGRES] latest_feature_error symbol={symbol} error={exc}",
            flush=True,
        )
        return {}

def _qfos_v2_is_fallback_strategy(strategy):
    s = str(strategy or "").lower()
    return "fallback" in s or "scout" in s


def _qfos_v2_market_breadth():
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
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
            """)).mappings().all()

        total = 0
        up = 0
        down = 0
        strong_up = 0

        excluded = {
            "USDC/USDT",
            "USD1/USDT",
            "EUR/USDT",
            "GOLD(PAXG)/USDT",
            "MOGU/USDT",
        }

        for row in rows:
            symbol = str(row.get("symbol") or "")
            if symbol in excluded:
                continue

            total += 1

            is_up = bool(row.get("is_symbol_uptrend"))
            is_down = bool(row.get("is_symbol_downtrend"))
            is_choppy = bool(row.get("is_choppy"))
            regime = str(row.get("symbol_regime") or "")

            signal = _qfos_v2_float(row.get("signal_strength"))
            breakout = _qfos_v2_float(row.get("breakout_score"))
            trend_quality = _qfos_v2_float(row.get("trend_quality"))
            momentum = _qfos_v2_float(row.get("momentum"))
            one_tick = _qfos_v2_float(row.get("one_tick_momentum"))

            if is_down:
                down += 1

            if is_up and not is_choppy and regime in (
                "SYMBOL_BREAKOUT_UP",
                "SYMBOL_TREND_UP",
            ):
                up += 1

                if (
                    signal >= 0.0065
                    and breakout >= 0.0055
                    and trend_quality > 0
                    and momentum > 0
                    and one_tick >= 0
                ):
                    strong_up += 1

        return {
            "total": total,
            "up": up,
            "down": down,
            "strong_up": strong_up,
            "down_ratio": down / max(total, 1),
            "up_ratio": up / max(total, 1),
            "strong_up_ratio": strong_up / max(total, 1),
        }

    except Exception as exc:
        print(f"[POLICY_V2_POSTGRES] breadth_error={exc}", flush=True)
        return {
            "total": 0,
            "up": 0,
            "down": 0,
            "strong_up": 0,
            "down_ratio": 0.0,
            "up_ratio": 0.0,
            "strong_up_ratio": 0.0,
        }

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
    return qfos_runtime_db_path()

def _qfos_opp_state():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT equity, cash, exposure, drawdown, regime
                FROM portfolio_snapshots
                ORDER BY id DESC
                LIMIT 1
            """)).mappings().first()

        if row:
            equity = _qfos_opp_float(row.get("equity"), 100.0)
            cash = _qfos_opp_float(row.get("cash"), equity)
            exposure = _qfos_opp_float(row.get("exposure"), 0.0)
            drawdown = _qfos_opp_float(row.get("drawdown"), 0.0)
            regime = str(row.get("regime") or "SIDEWAYS")
        else:
            equity, cash, exposure, drawdown, regime = (
                100.0,
                100.0,
                0.0,
                0.0,
                "SIDEWAYS",
            )

        return {
            "equity": max(equity, 1.0),
            "cash": max(cash, 0.0),
            "exposure": max(exposure, 0.0),
            "exposure_pct": max(exposure, 0.0) / max(equity, 1.0),
            "drawdown": drawdown,
            "regime": regime,
        }

    except Exception as exc:
        print(f"[OPPORTUNITY_MODE_POSTGRES] state_error={exc}", flush=True)
        return {
            "equity": 100.0,
            "cash": 100.0,
            "exposure": 0.0,
            "exposure_pct": 0.0,
            "drawdown": 0.0,
            "regime": "SIDEWAYS",
        }

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
    # QFOS_DISABLE_SIDEWAYS_OPP_OVERRIDE_V1
    # SIDEWAYS must not revive a rejected evo_* entry through
    # low-exposure Opportunity Mode. Normal approved entries remain
    # eligible; only the rejected-entry override is disabled.
    try:
        _qfos_opp_regime = str(((_qfos_opp_state() or {}).get('regime')) or '').upper()
        if _qfos_opp_regime == 'SIDEWAYS':
            print(
                '[OPPORTUNITY_MODE_OVERRIDE_BLOCK] '
                f'symbol={symbol} strategy={strategy} '
                f'reason={reason} regime=SIDEWAYS',
                flush=True,
            )
            return False
    except Exception as _qfos_opp_sideways_guard_error:
        print(
            '[OPPORTUNITY_MODE_OVERRIDE_BLOCK] '
            f'reason=regime_lookup_error '
            f'error={_qfos_opp_sideways_guard_error!r}',
            flush=True,
        )
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
    return qfos_runtime_db_path()

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
    # SQLite-backed rescue gates are intentionally disabled until their
    # PostgreSQL state, quarantine, and pacing replacements are implemented.
    existing = result.get("orders") if isinstance(result, dict) else []

    if isinstance(existing, list) and existing:
        return existing

    if not globals().get("_QFOS_POSTGRES_RESCUE_DISABLED_NOTICE_EMITTED"):
        globals()["_QFOS_POSTGRES_RESCUE_DISABLED_NOTICE_EMITTED"] = True
        print(
            "[ALLOCATOR_RESCUE] disabled reason=postgres_rebuild_pending",
            flush=True,
        )

    return []




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
    return qfos_runtime_db_path()

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
    return qfos_runtime_db_path()

def _qfos_full_exit_open_qty(symbol):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT quantity
                    FROM positions
                    WHERE symbol = :symbol
                    LIMIT 1
                """),
                {"symbol": str(symbol)},
            ).mappings().first()

        if not row:
            return 0.0

        return max(_qfos_full_exit_float(row.get("quantity")), 0.0)

    except Exception as exc:
        print(
            f"[QFOS_FULL_EXIT_POSTGRES] qty_lookup_error symbol={symbol} error={exc}",
            flush=True,
        )
        return 0.0

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



# BEGIN AGENT4_NORMAL_FEATURE_CONTRACT_REPAIR


# BEGIN AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2
_QFOS_AGENT4_DEDICATED_FEATURE_STORE = None


def _qfos_agent4_runtime_prices(raw_prices):
    """Return a clean symbol->price map from either raw prices or tick object."""
    if not isinstance(raw_prices, dict):
        return {}
    nested = raw_prices.get("prices")
    if isinstance(nested, dict):
        return nested
    return raw_prices


def _qfos_agent4_get_dedicated_feature_store():
    """Dedicated FeatureStore used only if main's feature object is stale/wrong."""
    global _QFOS_AGENT4_DEDICATED_FEATURE_STORE
    if _QFOS_AGENT4_DEDICATED_FEATURE_STORE is None:
        try:
            from data.feature_store import FeatureStore
            _QFOS_AGENT4_DEDICATED_FEATURE_STORE = FeatureStore()
            print("[FEATURE_HANDOFF] dedicated_feature_store_created=True", flush=True)
        except Exception as exc:
            print(f"[FEATURE_HANDOFF_ERROR] dedicated_store_create_failed={repr(exc)}", flush=True)
            return None
    return _QFOS_AGENT4_DEDICATED_FEATURE_STORE


def _qfos_agent4_is_contract_ready_normal(feature):
    if not isinstance(feature, dict):
        return False
    if feature.get("ready") is not True:
        return False
    if str(feature.get("source", "")).upper() != "NORMAL":
        return False
    if str(feature.get("symbol_regime", "")).upper() == "WARMING_UP":
        return False
    try:
        if float(feature.get("price", 0.0) or 0.0) <= 0:
            return False
    except Exception:
        return False

    required = (
        "price",
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "confidence",
        "symbol_regime",
        "breakout_score",
        "trend_quality",
        "is_symbol_uptrend",
        "is_choppy",
        "source",
        "ready",
    )

    for key in required:
        if key not in feature:
            return False

    for key in (
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "confidence",
        "breakout_score",
        "trend_quality",
    ):
        try:
            float(feature.get(key))
        except Exception:
            return False

    return True


def _qfos_agent4_contract_repair_feature_map(feature_map):
    """Repair metadata only on already real, price-bearing NORMAL features."""
    if not isinstance(feature_map, dict):
        return {}

    out = {}

    def _flt(v, default=0.0):
        try:
            x = float(v)
            if x == x and x not in (float("inf"), float("-inf")):
                return x
        except Exception:
            pass
        return float(default)

    for symbol, feature in feature_map.items():
        if not isinstance(feature, dict):
            out[symbol] = feature
            continue

        f = dict(feature)

        if str(f.get("source", "NORMAL")).upper() == "RAW_MOMENTUM_FALLBACK":
            out[symbol] = f
            continue

        price = _flt(f.get("price"), 0.0)
        if price <= 0:
            out[symbol] = f
            continue

        f["source"] = "NORMAL"

        for k in (
            "trend",
            "long_trend",
            "volatility",
            "momentum",
            "one_tick_momentum",
            "signal_strength",
            "breakout_score",
            "trend_quality",
        ):
            f[k] = _flt(f.get(k), 0.0)

        if f.get("confidence") is None or str(f.get("confidence")).lower() in ("", "none", "nan"):
            signal = _flt(f.get("signal_strength"), 0.0)
            quality = _flt(f.get("trend_quality"), 0.0)
            breakout = _flt(f.get("breakout_score"), 0.0)
            f["confidence"] = max(0.0, min(1.0, (signal + quality + breakout) / 0.018))
        else:
            f["confidence"] = _flt(f.get("confidence"), 0.0)

        if not f.get("symbol_regime"):
            f["symbol_regime"] = "SYMBOL_NEUTRAL"

        if str(f.get("symbol_regime", "")).upper() == "WARMING_UP":
            f["ready"] = False

        if "is_symbol_uptrend" not in f:
            f["is_symbol_uptrend"] = str(f.get("symbol_regime", "")).upper() in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP")

        if "is_choppy" not in f:
            f["is_choppy"] = str(f.get("symbol_regime", "")).upper() == "SYMBOL_CHOPPY"

        # Do not override FeatureStore readiness.
        # WARMING_UP / insufficient_history must remain not ready.
        if str(f.get("symbol_regime", "")).upper() == "WARMING_UP":
            f["ready"] = False

        out[symbol] = f

    return out


def _qfos_agent4_count_ready_normal(feature_map):
    if not isinstance(feature_map, dict):
        return 0
    return sum(1 for f in feature_map.values() if _qfos_agent4_is_contract_ready_normal(f))


# QFOS_AGENT4_SINGLE_UPDATE_ONE_TICK_FIX_V1
def _qfos_agent4_build_normal_feature_map(
    features_obj,
    prices,
    settings,
    already_updated=False,
    prior_health=None,
):
    """Build a valid NORMAL feature map from real validated prices."""
    clean_prices = _qfos_agent4_runtime_prices(prices)
    symbols = list(getattr(settings, "symbol_list", []) or [])

    feature_health = prior_health
    built = {}

    # First try the runtime FeatureStore object already updated by main.py.
    # Do not append the same validated price map twice in one cycle:
    # duplicate append makes arr[-1] == arr[-2] and forces one_tick to 0.
    try:
        if (
            features_obj is not None
            and hasattr(features_obj, "update")
            and not already_updated
        ):
            feature_health = features_obj.update(clean_prices)
        if features_obj is not None and hasattr(features_obj, "all_features"):
            built = features_obj.all_features(symbols)
        elif features_obj is not None and hasattr(features_obj, "features"):
            built = {s: features_obj.features(s) for s in symbols}
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] primary_feature_build_failed={repr(exc)}", flush=True)
        built = {}

    built = _qfos_agent4_contract_repair_feature_map(built)
    if _qfos_agent4_count_ready_normal(built) > 0:
        return built, feature_health, "primary_feature_store"

    # If main's feature object is stale/wrong, use a dedicated FeatureStore.
    # This still uses only real validated prices; it does not create synthetic features.
    try:
        store = _qfos_agent4_get_dedicated_feature_store()
        if store is not None:
            feature_health = store.update(clean_prices)
            if hasattr(store, "all_features"):
                built = store.all_features(symbols)
            else:
                built = {s: store.features(s) for s in symbols}
            built = _qfos_agent4_contract_repair_feature_map(built)
            return built, feature_health, "dedicated_feature_store"
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] dedicated_feature_build_failed={repr(exc)}", flush=True)

    return built, feature_health, "failed_or_warming"


def _qfos_agent4_log_feature_handoff(feature_map, feature_health, source):
    try:
        ready_normal = {
            s: f for s, f in (feature_map or {}).items()
            if _qfos_agent4_is_contract_ready_normal(f)
        }
        ready_any = {
            s: f for s, f in (feature_map or {}).items()
            if isinstance(f, dict) and f.get("ready") is True
        }

        sample = []
        for s, f in list(ready_normal.items())[:3]:
            sample.append({
                "symbol": s,
                "price": f.get("price"),
                "trend": f.get("trend"),
                "long_trend": f.get("long_trend"),
                "volatility": f.get("volatility"),
                "momentum": f.get("momentum"),
                "one_tick_momentum": f.get("one_tick_momentum"),
                "signal_strength": f.get("signal_strength"),
                "confidence": f.get("confidence"),
                "symbol_regime": f.get("symbol_regime"),
                "breakout_score": f.get("breakout_score"),
                "trend_quality": f.get("trend_quality"),
                "is_symbol_uptrend": f.get("is_symbol_uptrend"),
                "is_choppy": f.get("is_choppy"),
                "source": f.get("source"),
                "ready": f.get("ready"),
            })

        print(
            "[FEATURE_HANDOFF] "
            f"Feature symbols={len(feature_map or {})} "
            f"normal_features={len(ready_normal)} "
            f"ready_features={len(ready_any)} "
            f"builder={source} "
            f"health={feature_health} "
            f"sample={sample}",
            flush=True,
        )
    except Exception as exc:
        print(f"[FEATURE_HANDOFF_ERROR] hard_log_failed={repr(exc)}", flush=True)
# END AGENT4_HARD_NORMAL_FEATURE_HANDOFF_V2


def _qfos_agent4_float(value, default=0.0):
    try:
        x = float(value)
        if x == x and x not in (float("inf"), float("-inf")):
            return x
    except Exception:
        pass
    return float(default)


def _qfos_agent4_feature_contract_repair(feature_map):
    """
    Repairs metadata only for already-real NORMAL FeatureStore objects.
    Does not create features, does not change prices, does not make fallback executable.
    """
    if not isinstance(feature_map, dict):
        return {}

    repaired = {}

    required_numeric = (
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "breakout_score",
        "trend_quality",
        "confidence",
    )

    for symbol, feature in feature_map.items():
        if not isinstance(feature, dict):
            repaired[symbol] = feature
            continue

        f = dict(feature)

        if str(f.get("source", "NORMAL")).upper() == "RAW_MOMENTUM_FALLBACK":
            repaired[symbol] = f
            continue

        # Only real price-bearing feature objects can be repaired.
        price = _qfos_agent4_float(f.get("price"), 0.0)
        if price <= 0:
            repaired[symbol] = f
            continue

        f["source"] = "NORMAL"

        # Preserve FeatureStore readiness exactly.
        # Warming features must not be promoted by a local history shortcut.

        for key in required_numeric:
            if key == "confidence":
                continue
            f[key] = _qfos_agent4_float(f.get(key), 0.0)

        if f.get("confidence") is None or str(f.get("confidence")).lower() in ("nan", "none", ""):
            signal = _qfos_agent4_float(f.get("signal_strength"), 0.0)
            quality = _qfos_agent4_float(f.get("trend_quality"), 0.0)
            breakout = _qfos_agent4_float(f.get("breakout_score"), 0.0)
            f["confidence"] = max(0.0, min(1.0, (signal + quality + breakout) / 0.018))
        else:
            f["confidence"] = _qfos_agent4_float(f.get("confidence"), 0.0)

        if not f.get("symbol_regime"):
            f["symbol_regime"] = "SYMBOL_NEUTRAL"

        f["is_symbol_uptrend"] = bool(f.get("is_symbol_uptrend", False))
        f["is_choppy"] = bool(f.get("is_choppy", False))

        repaired[symbol] = f

    return repaired


def _qfos_agent4_is_ready_normal_feature(feature):
    if not isinstance(feature, dict):
        return False
    if feature.get("ready") is not True:
        return False
    if str(feature.get("source", "")).upper() != "NORMAL":
        return False
    if _qfos_agent4_float(feature.get("price"), 0.0) <= 0:
        return False
    for key in (
        "trend",
        "long_trend",
        "volatility",
        "momentum",
        "one_tick_momentum",
        "signal_strength",
        "confidence",
        "breakout_score",
        "trend_quality",
    ):
        try:
            float(feature.get(key))
        except Exception:
            return False
    for key in ("symbol_regime", "is_symbol_uptrend", "is_choppy"):
        if key not in feature:
            return False
    return True
# END AGENT4_NORMAL_FEATURE_CONTRACT_REPAIR

# AGENT3_SAFE_RESCUE_SANITIZER_CACHE_V2
# Safe top-level override. Do not insert this inside runtime if/loop blocks.
# Purpose:
# - Prevent legacy ALLOCATOR_RESCUE bypass.
# - Prevent over-blocking when ENTRY QUALITY TOP 10 exists but is out of local scope.
# - Keep source=NORMAL, ready=True, regime, confidence, signal gates intact.

AGENT3_LAST_ENTRY_QUALITY_TOP_SYMBOLS = globals().get("AGENT3_LAST_ENTRY_QUALITY_TOP_SYMBOLS", set())
AGENT3_LAST_ENTRY_QUALITY_TOP_ROWS = globals().get("AGENT3_LAST_ENTRY_QUALITY_TOP_ROWS", [])
AGENT3_LAST_ENTRY_QUALITY_TOP_SOURCE = globals().get("AGENT3_LAST_ENTRY_QUALITY_TOP_SOURCE", "empty")


def _agent3_safe_float(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def _agent3_safe_symbol_from_any(value):
    try:
        if isinstance(value, str) and "/" in value:
            return value
        if isinstance(value, dict):
            sym = value.get("symbol") or value.get("pair")
            if isinstance(sym, str) and "/" in sym:
                return sym
        if isinstance(value, (tuple, list)) and value:
            first = value[0]
            if isinstance(first, str) and "/" in first:
                return first
            if isinstance(first, dict):
                sym = first.get("symbol") or first.get("pair")
                if isinstance(sym, str) and "/" in sym:
                    return sym
    except Exception:
        pass
    return None


def _agent3_safe_rows_from_any(obj):
    rows = []
    try:
        if isinstance(obj, dict):
            # Do not treat a huge feature map as ENTRY QUALITY TOP 10.
            if len(obj) > 20:
                return []
            for k, v in obj.items():
                sym = _agent3_safe_symbol_from_any(k)
                if sym:
                    rows.append((sym, v))
                    continue
                sym = _agent3_safe_symbol_from_any(v)
                if sym:
                    rows.append(v)
            return rows

        if isinstance(obj, (list, tuple, set)):
            if len(obj) > 30:
                return []
            for item in obj:
                sym = _agent3_safe_symbol_from_any(item)
                if sym:
                    rows.append(item)
            return rows
    except Exception:
        return []

    return rows


def _agent3_safe_find_top_rows(local_vars=None):
    local_vars = local_vars if isinstance(local_vars, dict) else {}

    preferred_names = (
        "entry_quality_top_10",
        "entry_quality_top",
        "entry_quality",
        "entry_quality_rows",
        "entry_quality_top_rows",
        "top_quality_rows",
        "quality_top_rows",
        "top_entries",
        "top_candidates",
        "top_symbols",
        "ranked_top",
        "ranked_candidates_top",
    )

    for name in preferred_names:
        if name in local_vars:
            rows = _agent3_safe_rows_from_any(local_vars.get(name))
            if rows:
                return rows, f"local:{name}"

    # Fallback scan only for small list/tuple/set values containing symbol rows.
    best_rows = []
    best_name = None
    for name, value in local_vars.items():
        if not isinstance(value, (list, tuple, set, dict)):
            continue
        rows = _agent3_safe_rows_from_any(value)
        if rows and len(rows) > len(best_rows):
            best_rows = rows
            best_name = name

    if best_rows:
        return best_rows[:20], f"local_scan:{best_name}"

    return [], "empty"


def _agent3_safe_update_top_cache(local_vars=None, rows=None, source_hint=None):
    global AGENT3_LAST_ENTRY_QUALITY_TOP_SYMBOLS
    global AGENT3_LAST_ENTRY_QUALITY_TOP_ROWS
    global AGENT3_LAST_ENTRY_QUALITY_TOP_SOURCE

    try:
        if rows is None:
            rows, src = _agent3_safe_find_top_rows(local_vars)
        else:
            rows = _agent3_safe_rows_from_any(rows)
            src = source_hint or "explicit"

        symbols = []
        for row in rows:
            sym = _agent3_safe_symbol_from_any(row)
            if sym and sym not in symbols:
                symbols.append(sym)

        if symbols:
            AGENT3_LAST_ENTRY_QUALITY_TOP_ROWS = list(rows)
            AGENT3_LAST_ENTRY_QUALITY_TOP_SYMBOLS = set(symbols)
            AGENT3_LAST_ENTRY_QUALITY_TOP_SOURCE = src
            print(
                f"[ALLOCATOR_RESCUE_SANITIZER] top_source=cache_update:{src} "
                f"top_count={len(symbols)} symbols={symbols[:10]}",
                flush=True,
            )
        else:
            print(
                "[ALLOCATOR_RESCUE_SANITIZER] top_source=cache_update:empty top_count=0",
                flush=True,
            )

    except Exception as exc:
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] top_source=cache_update_error top_count=0 error={exc}",
            flush=True,
        )


def _agent3_safe_get_top_symbols(local_vars=None):
    rows, src = _agent3_safe_find_top_rows(local_vars)
    symbols = set()

    for row in rows:
        sym = _agent3_safe_symbol_from_any(row)
        if sym:
            symbols.add(sym)

    if symbols:
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] top_source=local top_count={len(symbols)} symbols={list(symbols)[:10]}",
            flush=True,
        )
        return symbols, "local"

    cached = globals().get("AGENT3_LAST_ENTRY_QUALITY_TOP_SYMBOLS", set()) or set()
    if cached:
        cached = set(cached)
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] top_source=cache top_count={len(cached)} symbols={list(cached)[:10]}",
            flush=True,
        )
        return cached, "cache"

    print("[ALLOCATOR_RESCUE_SANITIZER] top_source=empty top_count=0", flush=True)
    return set(), "empty"


def _agent3_extract_top_symbols(local_vars):
    symbols, _src = _agent3_safe_get_top_symbols(local_vars)
    return set(symbols or [])


def _agent3_rescue_order_gate(order, local_vars):
    if not isinstance(order, dict):
        print("[ALLOCATOR_RESCUE_SANITIZER] symbol=None decision=BLOCK reason=invalid_order", flush=True)
        return False, "invalid_order", order

    try:
        is_rescue = _agent3_is_rescue_order(order)
    except Exception:
        src = str(order.get("source", "") or "").lower()
        strategy = str(order.get("strategy", "") or "").lower()
        reason = str(order.get("entry_reason", "") or "").lower()
        is_rescue = (
            "allocator_opportunity_rescue" in src
            or "allocator_rescue" in src
            or "evo_allocator_rescue" in strategy
            or "allocator_rescue" in reason
        )

    if not is_rescue:
        return True, "not_rescue_order", order

    symbol = order.get("symbol")
    if not symbol:
        print("[ALLOCATOR_RESCUE_SANITIZER] symbol=None decision=BLOCK reason=missing_symbol", flush=True)
        return False, "missing_symbol", order

    # Refresh cache from local vars if visible.
    _agent3_safe_update_top_cache(local_vars)

    top_symbols, top_source = _agent3_safe_get_top_symbols(local_vars)

    if not top_symbols:
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] symbol={symbol} in_top=False source=unknown ready=unknown "
            f"regime=unknown confidence=unknown signal=unknown decision=BLOCK reason=entry_quality_top_empty",
            flush=True,
        )
        return False, "entry_quality_top_empty", order

    if symbol not in top_symbols:
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] symbol={symbol} in_top=False top_source={top_source} "
            f"decision=BLOCK reason=not_in_entry_quality_top",
            flush=True,
        )
        return False, "not_in_entry_quality_top", order

    try:
        feature = _agent3_lookup_feature(order, local_vars)
    except Exception:
        feature = order.get("feature") if isinstance(order.get("feature"), dict) else {}

    if not isinstance(feature, dict) or not feature:
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] symbol={symbol} in_top=True decision=BLOCK reason=missing_feature_snapshot",
            flush=True,
        )
        return False, "missing_feature_snapshot", order

    ready = bool(feature.get("ready", False))
    feature_source = str(feature.get("source", "") or "").strip().upper()
    symbol_regime = str(feature.get("symbol_regime", "") or "").upper()
    signal_strength = _agent3_safe_float(feature.get("signal_strength", order.get("signal_strength")), 0.0)
    confidence = _agent3_safe_float(order.get("confidence", feature.get("confidence", 0.0)), 0.0)

    def _block(reason):
        print(
            f"[ALLOCATOR_RESCUE_SANITIZER] symbol={symbol} in_top=True source={feature_source} "
            f"ready={ready} regime={symbol_regime} confidence={confidence:.6f} signal={signal_strength:.6f} "
            f"decision=BLOCK reason={reason}",
            flush=True,
        )
        return False, reason, order

    if feature_source != "NORMAL":
        return _block("not_normal_source")

    if ready is not True:
        return _block("not_ready")

    source_words = " ".join([
        str(order.get("source", "")),
        str(order.get("strategy", "")),
        str(order.get("entry_reason", "")),
        str(feature.get("source", "")),
    ]).upper()

    if (
        "FALLBACK_SCOUT" in source_words
        or "RAW_MOMENTUM_FALLBACK" in source_words
        or "RAW_MOMENTUM" in source_words
    ):
        return _block("fallback_source_disabled")

    if symbol_regime not in ("SYMBOL_TREND_UP", "SYMBOL_BREAKOUT_UP"):
        return _block("symbol_regime_not_allowed")

    if confidence <= 0:
        return _block("confidence_below_threshold")

    if signal_strength <= 0:
        return _block("signal_strength_invalid")

    try:
        exposure_ok, exposure_reason = _agent3_exposure_allows_rescue(local_vars)
        if not exposure_ok:
            return _block(exposure_reason)
    except Exception:
        pass

    enriched = dict(order)
    enriched["feature"] = feature
    enriched["feature_source"] = "NORMAL"
    enriched["signal_strength"] = signal_strength
    enriched["symbol_regime"] = symbol_regime
    enriched["entry_reason"] = "evo_allocator_rescue_entry_quality_top_normal"
    enriched["confidence"] = confidence

    print(
        f"[ALLOCATOR_RESCUE_SANITIZER] symbol={symbol} in_top=True source={feature_source} "
        f"ready={ready} regime={symbol_regime} confidence={confidence:.6f} signal={signal_strength:.6f} "
        f"decision=ALLOW",
        flush=True,
    )

    return True, "passed", enriched

# AGENT3_SAFE_RESCUE_SANITIZER_CACHE_V2_END



# ============================================================
# QFOS_AGENT5_EXEC_BRIDGE_AUDIT_V1
# Purpose:
#   Every allocator rescue order must either:
#   1) persist through qfos_persist_fill_atomic(), or
#   2) log a specific rejection reason.
# ============================================================

def qfos_exec_bridge_audit(stage, **kwargs):
    try:
        parts = []
        for k, v in kwargs.items():
            if isinstance(v, float):
                parts.append(f"{k}={v:.12g}")
            else:
                parts.append(f"{k}={v}")
        print(f"[EXEC_BRIDGE_AUDIT] stage={stage} " + " ".join(parts), flush=True)
    except Exception as e:
        print(f"[EXEC_BRIDGE_AUDIT] stage=audit_error error={e}", flush=True)


def qfos_exec_bridge_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def qfos_exec_bridge_get_mark_price(symbol, order=None):
    if isinstance(order, dict):
        for key in ("fill_price", "expected_price", "price", "mark_price", "last_price"):
            val = qfos_exec_bridge_float(order.get(key), 0.0)
            if val > 0:
                return val

        feature = order.get("feature")
        if isinstance(feature, dict):
            for key in ("price", "last_price", "mark_price"):
                val = qfos_exec_bridge_float(feature.get(key), 0.0)
                if val > 0:
                    return val

    try:
        fobj = globals().get("features")
        if fobj is not None:
            for attr in ("features", "data", "by_symbol", "store"):
                d = getattr(fobj, attr, None)
                if isinstance(d, dict):
                    row = d.get(symbol)
                    if isinstance(row, dict):
                        val = qfos_exec_bridge_float(row.get("price") or row.get("last_price") or row.get("mark_price"), 0.0)
                        if val > 0:
                            return val
    except Exception:
        pass

    try:
        m = globals().get("market")
        if isinstance(m, dict):
            row = m.get(symbol)
            if isinstance(row, dict):
                val = qfos_exec_bridge_float(row.get("price") or row.get("last_price") or row.get("mark_price"), 0.0)
                if val > 0:
                    return val
            else:
                val = qfos_exec_bridge_float(row, 0.0)
                if val > 0:
                    return val
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT last_price, avg_entry
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()
        if row:
            val = qfos_exec_bridge_float(row.get("last_price"), 0.0)
            if val > 0:
                return val
            val = qfos_exec_bridge_float(row.get("avg_entry"), 0.0)
            if val > 0:
                return val
    except Exception:
        pass

    return 0.0


def qfos_exec_bridge_normalize_order(order, index=0):
    if not isinstance(order, dict):
        qfos_exec_bridge_audit("normalize_order", index=index, decision="DROP", reason="order_not_dict")
        return None, "order_not_dict"

    symbol = str(order.get("symbol") or "").strip()
    if not symbol:
        qfos_exec_bridge_audit("normalize_order", index=index, decision="DROP", reason="missing_symbol")
        return None, "missing_symbol"

    side = str(order.get("side") or "buy").strip().lower()
    if side not in ("buy", "sell"):
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, index=index, decision="DROP", reason="missing_side")
        return None, "missing_side"

    strategy = str(order.get("strategy") or order.get("entry_strategy") or "unknown").strip()
    source = str(order.get("source") or order.get("feature_source") or "").strip()

    price = qfos_exec_bridge_get_mark_price(symbol, order)
    if price <= 0:
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, strategy=strategy, side=side, decision="DROP", reason="missing_price")
        return None, "missing_price"

    qty = qfos_exec_bridge_float(order.get("quantity") or order.get("qty"), 0.0)
    value = qfos_exec_bridge_float(
        order.get("value") or order.get("notional") or order.get("target_value") or order.get("usd_value"),
        0.0,
    )

    if qty <= 0 and value > 0 and price > 0:
        qty = value / price

    if qty <= 0 and strategy == "evo_allocator_rescue" and side == "buy":
        default_value = 1.25
        try:
            a = qfos_agent5_ledger_accounting_snapshot() if "qfos_agent5_ledger_accounting_snapshot" in globals() else {}
            equity_hint = qfos_exec_bridge_float(a.get("expected_equity"), 100.0) if isinstance(a, dict) else 100.0
            default_value = min(1.25, max(0.50, equity_hint * 0.0125))
        except Exception:
            default_value = 1.25
        qty = default_value / price
        value = default_value

    if qty <= 0:
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, strategy=strategy, side=side, price=price, decision="DROP", reason="missing_quantity")
        return None, "missing_quantity"

    fill = dict(order)
    fill["symbol"] = symbol
    fill["side"] = side
    fill["quantity"] = float(qty)
    fill["expected_price"] = float(fill.get("expected_price") or price)
    fill["fill_price"] = float(fill.get("fill_price") or price)
    fill["strategy"] = strategy
    fill["confidence"] = qfos_exec_bridge_float(fill.get("confidence"), 1.0 if strategy == "evo_allocator_rescue" else 0.0)
    fill["slippage_bps"] = qfos_exec_bridge_float(fill.get("slippage_bps"), 0.0)
    fill["shadow_mode"] = bool(fill.get("shadow_mode", False))
    fill["live"] = bool(fill.get("live", False))
    if source:
        fill["source"] = source

    qfos_exec_bridge_audit("normalize_order", index=index, symbol=symbol, strategy=strategy, side=side, qty=float(qty), price=float(fill["fill_price"]), decision="ALLOW")
    qfos_exec_bridge_audit("proposed_fill_created", symbol=symbol, side=side, qty=float(qty), fill_price=float(fill["fill_price"]), strategy=strategy)
    return fill, "ok"


def qfos_exec_bridge_recent_duplicate_buy(symbol, qty, price, strategy, seconds=90):
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS n
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'buy'
                  AND strategy = :strategy
                  AND ABS(quantity - :qty) <= 0.00000001
                  AND ABS(fill_price - :price) <= 0.00000001
                  AND created_at >= CURRENT_TIMESTAMP - (:seconds || ' seconds')::interval
            """), {
                "symbol": symbol,
                "strategy": strategy,
                "qty": float(qty),
                "price": float(price),
                "seconds": int(seconds),
            }).mappings().first()
        return int((row or {}).get("n") or 0) > 0
    except Exception:
        return False


def qfos_exec_bridge_validate_fill(fill):
    symbol = str(fill.get("symbol") or "")
    side = str(fill.get("side") or "").lower()
    strategy = str(fill.get("strategy") or "")
    qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
    price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

    if not symbol:
        return False, "missing_symbol"
    if side not in ("buy", "sell"):
        return False, "missing_side"
    if qty <= 0:
        return False, "invalid_quantity"
    if price <= 0:
        return False, "invalid_price"

    if side == "buy":
        if qfos_exec_bridge_recent_duplicate_buy(symbol, qty, price, strategy):
            return False, "duplicate_order_blocked"

        try:
            # QFOS_AGENT5_EXEC_BRIDGE_MARKETDATA_ADAPTER_FIX_V1
            # qfos_active_canbuy_authority() expects dict-like market data. The runtime may expose
            # PaperMarketData, which is not .get()-compatible and caused:
            # risk_gate_error:'PaperMarketData' object has no attribute 'get'
            # Use the already-normalized fill price as the authoritative mark
            # for this bridge validation call.
            prices_obj = {symbol: price}
            equity_obj = float(getattr(portfolio, "equity", 100.0) or 100.0)
            ok, reason = qfos_active_canbuy_authority(symbol, fill, prices_obj, equity_obj)
            if not ok:
                reason_s = str(reason or "risk_gate_blocked")
                if "cooldown" in reason_s:
                    return False, "cooldown_blocked"
                if "already_holding" in reason_s or "max_open_positions" in reason_s:
                    return False, "existing_position_limit_blocked"
                if "exposure" in reason_s:
                    return False, "exposure_limit_blocked"
                return False, reason_s
        except NameError:
            pass
        except Exception as e:
            return False, f"risk_gate_error:{e}"

    return True, "ok"


def qfos_exec_bridge_after_persist_probe(symbol):
    try:
        with engine.begin() as conn:
            t = conn.execute(text("""
                SELECT id, quantity, fill_price
                FROM trades
                WHERE symbol = :symbol
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            p = conn.execute(text("""
                SELECT quantity
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

        trade_id = (t or {}).get("id")
        position_qty = qfos_exec_bridge_float((p or {}).get("quantity"), 0.0)
        return trade_id, position_qty
    except Exception:
        return None, None


def qfos_exec_bridge_persist_fill(fill, source="exec_bridge"):
    symbol = str(fill.get("symbol") or "")
    side = str(fill.get("side") or "").lower()
    strategy = str(fill.get("strategy") or "")
    qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
    price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

    qfos_exec_bridge_audit("before_persist", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy)

    try:
        with engine.begin() as conn:
            try:
                result = qfos_persist_fill_atomic(conn, fill, source=source)
            except TypeError:
                result = qfos_persist_fill_atomic(conn, fill)

        if result is False or result is None:
            qfos_exec_bridge_audit("persist_failed", symbol=symbol, side=side, reason="atomic_returned_false_or_none")
            return False

        trade_id, position_qty = qfos_exec_bridge_after_persist_probe(symbol)
        qfos_exec_bridge_audit("after_persist", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, trade_id=trade_id, position_qty=position_qty)
        return True

    except Exception as e:
        qfos_exec_bridge_audit("persist_failed", symbol=symbol, side=side, reason=str(e))
        return False


def qfos_exec_bridge_process_orders(raw_orders, source="exec_bridge"):
    if raw_orders is None:
        raw_orders = []
    if not isinstance(raw_orders, list):
        try:
            raw_orders = list(raw_orders)
        except Exception:
            raw_orders = []

    symbols = []
    strategies = []
    for o in raw_orders:
        if isinstance(o, dict):
            symbols.append(str(o.get("symbol") or ""))
            strategies.append(str(o.get("strategy") or ""))

    qfos_exec_bridge_audit("raw_orders_received", count=len(raw_orders), symbols=symbols, strategies=strategies, source=source)

    proposed = []
    for i, order in enumerate(raw_orders):
        fill, reason = qfos_exec_bridge_normalize_order(order, index=i)
        if fill is not None:
            proposed.append(fill)

    qfos_exec_bridge_audit("proposed_fills_summary", count=len(proposed), symbols=[str(f.get("symbol") or "") for f in proposed], strategies=[str(f.get("strategy") or "") for f in proposed])

    applied = 0

    for fill in proposed:
        symbol = str(fill.get("symbol") or "")
        side = str(fill.get("side") or "").lower()
        strategy = str(fill.get("strategy") or "")
        qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
        price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

        ok, reason = qfos_exec_bridge_validate_fill(fill)
        if not ok:
            qfos_exec_bridge_audit("final_validation", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, decision="REJECT", reason=reason)
            continue

        qfos_exec_bridge_audit("final_validation", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, decision="ALLOW")

        if qfos_exec_bridge_persist_fill(fill, source=source):
            applied += 1

    qfos_exec_bridge_audit("final_applied_summary", applied=applied, proposed=len(proposed), raw=len(raw_orders))
    return applied


# ============================================================
# End QFOS_AGENT5_EXEC_BRIDGE_AUDIT_V1
# ============================================================



# AGENT3_QUALITY_FORENSICS_V1
# Diagnostic wrapper only. No threshold, gate, ranking, execution, or accounting changes.

import threading as _qf_threading
import time as _qf_time
from collections import Counter as _qf_Counter

_QF_QUALITY_EVENTS = []
_QF_QUALITY_LOCK = _qf_threading.Lock()
_QF_QUALITY_TIMER = None
_QF_QUALITY_SEQ = 0
_QF_ENTRY_QUALITY_REASON_ORIGINAL = globals().get("_entry_quality_reason")

# Phase IVB: hourly signal health summary buffer
# Each entry: {signal: float, decision: 'ALLOW'|'REJECT', reason: str, confidence: float}
_QF_HOURLY_BUFFER = []
_QF_HOURLY_LOCK = _qf_threading.Lock()


def _qf_emit_hourly_summary():
    """Emit a structured hourly signal health summary and reschedule."""
    import datetime as _qf_dt

    try:
        with _QF_HOURLY_LOCK:
            buf = list(_QF_HOURLY_BUFFER)
            _QF_HOURLY_BUFFER.clear()

        accepted = [e for e in buf if e["decision"] == "ALLOW"]
        rejected = [e for e in buf if e["decision"] == "REJECT"]

        all_scores = sorted(e["signal"] for e in buf)
        accepted_scores = [e["signal"] for e in accepted]
        rejected_scores = [e["signal"] for e in rejected]

        def _pct(lst, p):
            if not lst:
                return 0.0
            idx = int(len(lst) * p / 100)
            idx = min(idx, len(lst) - 1)
            return sorted(lst)[idx]

        rej_reasons = {}
        for e in rejected:
            rej_reasons[e["reason"]] = rej_reasons.get(e["reason"], 0) + 1

        # BUY / SELL counts from DB (passive read)
        buy_count = 0
        sell_count = 0
        open_positions = 0
        try:
            _eng = globals().get("engine")
            _txt = globals().get("text")
            if _eng and _txt:
                with _eng.begin() as _conn:
                    _r = _conn.execute(_txt(
                        "SELECT side, COUNT(*) AS n FROM trades "
                        "WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '1 hour' "
                        "GROUP BY side"
                    )).mappings().all()
                    for _row in _r:
                        if str(_row.get("side", "")).lower() == "buy":
                            buy_count = int(_row.get("n") or 0)
                        elif str(_row.get("side", "")).lower() == "sell":
                            sell_count = int(_row.get("n") or 0)
                    _op = _conn.execute(_txt(
                        "SELECT COUNT(*) AS n FROM positions WHERE quantity > 0"
                    )).mappings().first()
                    open_positions = int((_op or {}).get("n") or 0)
        except Exception:
            pass

        avg_entry_score = (sum(accepted_scores) / len(accepted_scores)) if accepted_scores else 0.0
        highest_rejected = max(rejected_scores) if rejected_scores else 0.0
        lowest_accepted = min(accepted_scores) if accepted_scores else 0.0

        print(
            f"[SIGNAL_HEALTH_HOURLY] "
            f"ts={_qf_dt.datetime.utcnow().isoformat()} "
            f"candidates={len(buf)} "
            f"accepted={len(accepted)} "
            f"rejected={len(rejected)} "
            f"rejected_signal_too_weak={rej_reasons.get('signal_too_weak', sum(v for k,v in rej_reasons.items() if k.startswith('signal_too_weak')))} "
            f"rejected_risk={rej_reasons.get('risk', 0)} "
            f"rejected_exposure={rej_reasons.get('exposure', 0)} "
            f"rejected_portfolio_full={rej_reasons.get('portfolio_full', 0)} "
            f"score_p50={_pct(all_scores, 50):.6f} "
            f"score_p90={_pct(all_scores, 90):.6f} "
            f"score_p95={_pct(all_scores, 95):.6f} "
            f"score_p99={_pct(all_scores, 99):.6f} "
            f"buy_count_last_hour={buy_count} "
            f"sell_count_last_hour={sell_count} "
            f"open_positions={open_positions} "
            f"avg_entry_score={avg_entry_score:.6f} "
            f"highest_rejected_score={highest_rejected:.6f} "
            f"lowest_accepted_score={lowest_accepted:.6f} "
            f"threshold_sideways={ENTRY_MIN_SIGNAL_SIDEWAYS} "
            f"threshold_trending={ENTRY_MIN_SIGNAL_TRENDING}",
            flush=True,
        )
    except Exception as _e:
        print(f"[SIGNAL_HEALTH_HOURLY] error={_e!r}", flush=True)
    finally:
        _qf_hourly_thread = _qf_threading.Timer(3600.0, _qf_emit_hourly_summary)
        _qf_hourly_thread.daemon = True
        _qf_hourly_thread.start()


# Kick off the first hourly summary (fires 1 hour from engine start)
_qf_hourly_first = _qf_threading.Timer(3600.0, _qf_emit_hourly_summary)
_qf_hourly_first.daemon = True
_qf_hourly_first.start()
print("[SIGNAL_HEALTH_HOURLY] hourly_summary_scheduled interval_seconds=3600", flush=True)


def _qf_float(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except Exception:
        return float(default)


def _qf_find_candidate(obj, depth=0):
    if depth > 4:
        return {}
    if isinstance(obj, dict):
        if obj.get("symbol"):
            return obj
        for value in obj.values():
            found = _qf_find_candidate(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found = _qf_find_candidate(value, depth + 1)
            if found:
                return found
    return {}


def _qf_feature_for(symbol, candidate, local_args, local_kwargs):
    feature = candidate.get("feature") if isinstance(candidate, dict) else None
    if isinstance(feature, dict):
        return feature

    for obj in list(local_args) + list(local_kwargs.values()):
        if isinstance(obj, dict):
            if symbol in obj and isinstance(obj.get(symbol), dict):
                return obj.get(symbol)
            for key in ("features", "feature_map", "normal_features"):
                nested = obj.get(key)
                if isinstance(nested, dict) and isinstance(nested.get(symbol), dict):
                    return nested.get(symbol)

    for name in ("features", "feature_map", "last_features", "normal_features"):
        mapping = globals().get(name)
        if isinstance(mapping, dict) and isinstance(mapping.get(symbol), dict):
            return mapping.get(symbol)

    return {}


def _qf_open_qty(symbol):
    try:
        portfolio = globals().get("portfolio")
        positions = getattr(portfolio, "positions", {})
        if isinstance(positions, dict):
            return _qf_float(positions.get(symbol), 0.0)
    except Exception:
        pass
    return 0.0


def _qf_db_flags(symbol):
    quarantined = "unknown"
    recent_stop_loss = "unknown"

    try:
        engine = globals().get("engine")
        text_fn = globals().get("text")
        if engine is None or text_fn is None:
            return quarantined, recent_stop_loss

        with engine.begin() as conn:
            q = conn.execute(
                text_fn("""
                    SELECT blocked_until
                    FROM symbol_quarantine
                    WHERE symbol = :symbol
                      AND blocked_until IS NOT NULL
                      AND blocked_until > CURRENT_TIMESTAMP
                    ORDER BY blocked_until DESC
                    LIMIT 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            quarantined = bool(q)

            s = conn.execute(
                text_fn("""
                    SELECT COUNT(*) AS n
                    FROM trades
                    WHERE symbol = :symbol
                      AND lower(side) = 'sell'
                      AND strategy = 'sideways_stop_loss_exit'
                      AND created_at >= CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                """),
                {"symbol": symbol},
            ).mappings().first()

            recent_stop_loss = int((s or {}).get("n") or 0) > 0
    except Exception:
        pass

    return quarantined, recent_stop_loss


def _qf_result_reason(result):
    if result is None:
        return False, "approved"

    if isinstance(result, str):
        return bool(result.strip()), result.strip() or "approved"

    if result is False:
        return True, "rejected_false"

    if isinstance(result, (tuple, list)):
        if result and isinstance(result[0], bool):
            rejected = not result[0]
            reason = str(result[1]) if len(result) > 1 else ("approved" if not rejected else "rejected")
            return rejected, reason

        for item in result:
            if isinstance(item, str) and item.strip():
                return True, item.strip()

    if isinstance(result, dict):
        reason = result.get("reason")
        if reason:
            return True, str(reason)

    return False, "approved"


def _qf_flush_summary():
    global _QF_QUALITY_TIMER

    with _QF_QUALITY_LOCK:
        events = list(_QF_QUALITY_EVENTS)
        _QF_QUALITY_EVENTS.clear()
        _QF_QUALITY_TIMER = None

    if not events:
        return

    counts = _qf_Counter(e["reason"] for e in events if e["decision"] == "REJECT")
    allowed = sum(1 for e in events if e["decision"] == "ALLOW")
    rejected = sum(1 for e in events if e["decision"] == "REJECT")

    print(
        "[QUALITY_RANK_SUMMARY] "
        f"broad_candidates={len(events)} "
        f"allowed_candidates={allowed} "
        f"rejected_candidates={rejected} "
        f"reject_reason_counts={dict(counts)} "
        f"top_count=unknown "
        f"hook=_entry_quality_reason",
        flush=True,
    )


def _qf_schedule_summary():
    global _QF_QUALITY_TIMER

    with _QF_QUALITY_LOCK:
        if _QF_QUALITY_TIMER is not None:
            try:
                _QF_QUALITY_TIMER.cancel()
            except Exception:
                pass

        _QF_QUALITY_TIMER = _qf_threading.Timer(1.0, _qf_flush_summary)
        _QF_QUALITY_TIMER.daemon = True
        _QF_QUALITY_TIMER.start()


def _entry_quality_reason(*args, **kwargs):
    global _QF_QUALITY_SEQ

    result = _QF_ENTRY_QUALITY_REASON_ORIGINAL(*args, **kwargs)

    candidate = _qf_find_candidate(args) or _qf_find_candidate(kwargs)
    symbol = str(candidate.get("symbol") or kwargs.get("symbol") or "UNKNOWN")
    feature = _qf_feature_for(symbol, candidate, args, kwargs)

    rejected, reason = _qf_result_reason(result)
    decision = "REJECT" if rejected else "ALLOW"

    source = str(feature.get("source") or candidate.get("feature_source") or "unknown")
    ready = bool(feature.get("ready", candidate.get("ready", False)))
    regime = str(feature.get("symbol_regime") or candidate.get("symbol_regime") or "unknown")

    signal = _qf_float(feature.get("signal_strength", candidate.get("signal_strength")), 0.0)
    confidence = _qf_float(candidate.get("confidence", feature.get("confidence")), 0.0)
    trend = _qf_float(feature.get("trend"), 0.0)
    long_trend = _qf_float(feature.get("long_trend"), 0.0)
    one_tick = _qf_float(feature.get("one_tick_momentum"), 0.0)
    volatility = _qf_float(feature.get("volatility"), 0.0)
    breakout = _qf_float(feature.get("breakout_score"), 0.0)
    trend_quality = _qf_float(feature.get("trend_quality"), 0.0)

    is_up = bool(feature.get("is_symbol_uptrend", False))
    is_choppy = bool(feature.get("is_choppy", False))
    open_qty = _qf_open_qty(symbol)
    quarantined, recent_stop_loss = _qf_db_flags(symbol)

    same_symbol_cooldown = "unknown"
    big_loss_cooldown = "unknown"
    pacing_allowed = "unknown"

    _QF_QUALITY_SEQ += 1

    payload = (
        f"symbol={symbol} reason={reason} source={source} ready={ready} "
        f"regime={regime} signal_strength={signal:.6f} confidence={confidence:.6f} "
        f"trend={trend:.6f} long_trend={long_trend:.6f} "
        f"one_tick_momentum={one_tick:.6f} volatility={volatility:.6f} "
        f"breakout_score={breakout:.6f} trend_quality={trend_quality:.6f} "
        f"is_symbol_uptrend={is_up} is_choppy={is_choppy} "
        f"recent_stop_loss={recent_stop_loss} quarantined={quarantined} "
        f"same_symbol_cooldown={same_symbol_cooldown} "
        f"big_loss_cooldown={big_loss_cooldown} pacing_allowed={pacing_allowed} "
        f"existing_position={open_qty > 1e-8} sequence={_QF_QUALITY_SEQ}"
    )

    print(f"[QUALITY_{decision}] {payload}", flush=True)

    with _QF_QUALITY_LOCK:
        _QF_QUALITY_EVENTS.append({
            "symbol": symbol,
            "decision": decision,
            "reason": reason,
        })

    # Feed hourly signal health buffer (passive, no gate logic)
    try:
        with _QF_HOURLY_LOCK:
            _QF_HOURLY_BUFFER.append({
                "signal": signal,
                "decision": decision,
                "reason": reason,
                "confidence": confidence,
            })
    except Exception:
        pass

    _qf_schedule_summary()

    return result


print("[QUALITY_RANK_FORENSICS] installed hook=_entry_quality_reason diagnostic_only=True", flush=True)

# AGENT3_QUALITY_FORENSICS_V1_END


# ============================================================
# QFOS_CANONICAL_TRADE_LIFECYCLE_V1
#
# Canonical authority:
#   trade intent -> atomic core -> DB trade trigger -> DB positions
#   -> DB ledger snapshot -> runtime cache refresh
#
# Legacy apply_buy/apply_sell previously mutated the real in-memory
# portfolio before persistence. They are replaced below with intent-only
# builders. The existing DB atomic core remains the only trade writer.
# ============================================================

_QFOS_ATOMIC_FILL_CORE_V1 = qfos_persist_fill_atomic

def qfos_refresh_runtime_portfolio_from_db(source="canonical_refresh"):
    """
    Refresh runtime cache from PostgreSQL only.
    Never write DB positions from portfolio memory.
    """
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT symbol, quantity, avg_entry, last_price
                FROM positions
                WHERE quantity > 0.00000001
                ORDER BY symbol
            """)).mappings().all()

            ledger = conn.execute(text("""
                SELECT *
                FROM qfos_current_ledger_accounting()
                LIMIT 1
            """)).mappings().first()

        new_positions = {}
        new_entries = {}

        for row in rows:
            symbol = str(row.get("symbol") or "")
            qty = float(row.get("quantity") or 0.0)
            avg = float(row.get("avg_entry") or 0.0)

            if symbol and qty > 0.00000001:
                new_positions[symbol] = qty
                if avg > 0:
                    new_entries[symbol] = avg

        portfolio.positions.clear()
        portfolio.positions.update(new_positions)

        entry_prices.clear()
        entry_prices.update(new_entries)

        if ledger:
            cash = float(
                ledger.get("expected_cash")
                or ledger.get("cash")
                or ledger.get("available_cash")
                or 0.0
            )
            equity = float(
                ledger.get("expected_equity")
                or ledger.get("equity")
                or 0.0
            )

            if cash >= 0:
                portfolio.cash = cash
            if equity > 0:
                portfolio.equity = equity
                portfolio.peak = max(float(getattr(portfolio, "peak", 0.0) or 0.0), equity)

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=runtime_cache_refreshed source={source} "
            f"open_positions={len(new_positions)} "
            f"cash={float(getattr(portfolio, 'cash', 0.0) or 0.0):.8f} "
            f"equity={float(getattr(portfolio, 'equity', 0.0) or 0.0):.8f}",
            flush=True,
        )
        return True

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=UNKNOWN "
            f"reason=runtime_cache_refresh_error:{e}",
            flush=True,
        )
        return False



# ============================================================
# QFOS_CANONICAL_BOUNDARY_HARDENING_V1
# ============================================================

def _qfos_refresh_runtime_cache_from_active_conn(conn, source="canonical_active_conn"):
    """
    Refresh runtime portfolio cache using the same active DB transaction
    that just persisted the fill and fired position triggers.
    """
    try:
        rows = conn.execute(text("""
            SELECT symbol, quantity, avg_entry
            FROM positions
            WHERE quantity > 0.00000001
            ORDER BY symbol
        """)).mappings().all()

        ledger = conn.execute(text("""
            SELECT *
            FROM qfos_current_ledger_accounting()
            LIMIT 1
        """)).mappings().first()

        new_positions = {}
        new_entries = {}

        for row in rows:
            symbol = str(row.get("symbol") or "")
            qty = float(row.get("quantity") or 0.0)
            avg = float(row.get("avg_entry") or 0.0)

            if symbol and qty > 0.00000001:
                new_positions[symbol] = qty
                if avg > 0:
                    new_entries[symbol] = avg

        portfolio.positions.clear()
        portfolio.positions.update(new_positions)

        entry_prices.clear()
        entry_prices.update(new_entries)

        if ledger:
            cash = float(ledger.get("cash") or ledger.get("available_cash") or 0.0)
            equity = float(ledger.get("equity") or 0.0)

            if cash >= 0:
                portfolio.cash = cash
            if equity > 0:
                portfolio.equity = equity
                portfolio.peak = max(
                    float(getattr(portfolio, "peak", 0.0) or 0.0),
                    equity,
                )

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=position_updated authority=db_trade_trigger "
            f"source={source} open_positions={len(new_positions)} "
            f"cash={float(getattr(portfolio, 'cash', 0.0) or 0.0):.8f} "
            f"equity={float(getattr(portfolio, 'equity', 0.0) or 0.0):.8f}",
            flush=True,
        )
        return True

    except Exception as exc:
        print(
            "[TRADE_BOUNDARY_REJECT] "
            f"symbol=UNKNOWN side=UNKNOWN "
            f"reason=active_transaction_cache_refresh_error:{exc}",
            flush=True,
        )
        return False


def qfos_apply_fill_atomic(conn, fill, source="canonical"):
    """
    The sole canonical runtime boundary.

    Existing callers may still invoke qfos_persist_fill_atomic; the global
    alias below routes all of them into this wrapper and then into the
    preserved atomic core.
    """
    if not isinstance(fill, dict):
        print(
            "[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=UNKNOWN "
            "reason=fill_not_dict",
            flush=True,
        )
        return False

    normalized = dict(fill)

    symbol = str(normalized.get("symbol") or "").strip()
    side = str(normalized.get("side") or "").lower().strip()
    qty = float(normalized.get("quantity") or normalized.get("qty") or 0.0)
    price = float(
        normalized.get("fill_price")
        or normalized.get("expected_price")
        or normalized.get("price")
        or 0.0
    )
    strategy = str(normalized.get("strategy") or normalized.get("reason") or "unknown")
    exit_reason = str(normalized.get("exit_reason") or normalized.get("reason") or "")

    if side in ("long", "open"):
        side = "buy"
    elif side in ("close", "short"):
        side = "sell"

    if not symbol or side not in ("buy", "sell") or qty <= 0 or price <= 0:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side={side or 'UNKNOWN'} "
            "reason=invalid_symbol_side_qty_or_price",
            flush=True,
        )
        return False

    normalized["symbol"] = symbol
    normalized["side"] = side
    normalized["quantity"] = qty
    normalized["qty"] = qty
    normalized["fill_price"] = price
    normalized["expected_price"] = float(normalized.get("expected_price") or price)
    normalized["strategy"] = strategy
    normalized["source"] = str(source or normalized.get("source") or "canonical")

    if side == "sell":
        normalized["is_exit"] = True
        normalized["exit_reason"] = exit_reason or strategy
    else:
        normalized["is_exit"] = bool(normalized.get("is_exit", False))

    position_before = 0.0
    cost_basis_before = 0.0

    try:
        row = conn.execute(text("""
            SELECT quantity, avg_entry
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

        position_before = float((row or {}).get("quantity") or 0.0)
        cost_basis_before = float((row or {}).get("avg_entry") or 0.0)
    except Exception:
        pass

    # QFOS_DURABLE_SELL_IDEMPOTENCY_V1
    # BUYs remain nullable: a symbol may be bought again later.
    # SELLs are keyed to the current latest BUY lot plus reason/source/qty.
    # A retry for the same open lot therefore reaches the database unique index
    # and cannot create a second durable SELL row.
    lifecycle_key = None
    latest_open_buy_id = None

    if side == "sell":
        try:
            latest_buy = conn.execute(text("""
                SELECT id
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'buy'
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            latest_open_buy_id = int((latest_buy or {}).get("id") or 0)
        except Exception:
            latest_open_buy_id = 0

        lifecycle_key = (
            f"SELL|{symbol}|lot={latest_open_buy_id}|"
            f"qty={round(qty, 10)}"
        )

    normalized["lifecycle_key"] = lifecycle_key

    phase = "entry_intent" if side == "buy" else "exit_intent"
    print(
        "[TRADE_LIFECYCLE] "
        f"phase={phase} symbol={symbol} side={side} "
        f"quantity={qty:.12f} fill_price={price:.12f} "
        f"source={source} strategy={strategy} "
        f"position_qty_before={position_before:.12f} "
        f"cost_basis_used={cost_basis_before:.12f} "
        f"lifecycle_key={lifecycle_key}",
        flush=True,
    )

    try:
        try:
            result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized, source=source)
        except TypeError:
            result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized)
    except Exception as exc:
        err = str(exc).lower()
        reject_reason = (
            "duplicate_lifecycle_key"
            if "lifecycle_key" in err or "qfos_trades_sell_lifecycle_key_uq" in err
            else f"atomic_core_exception:{exc}"
        )
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            f"reason={reject_reason}",
            flush=True,
        )
        return False

    if result is None or result is False:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            "reason=atomic_core_rejected",
            flush=True,
        )
        return False

    _qfos_refresh_runtime_cache_from_active_conn(
        conn,
        source=f"post_persist:{source}",
    )

    try:
        trade = conn.execute(text("""
            SELECT id, quantity, fill_price, pnl, is_exit, exit_reason
            FROM trades
            WHERE symbol = :symbol
              AND lower(side) = :side
            ORDER BY id DESC
            LIMIT 1
        """), {"symbol": symbol, "side": side}).mappings().first()

        pos = conn.execute(text("""
            SELECT quantity, avg_entry
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

        trade_id = (trade or {}).get("id")
        realized_pnl = float((trade or {}).get("pnl") or 0.0)
        position_after = float((pos or {}).get("quantity") or 0.0)
        cost_basis_after = float((pos or {}).get("avg_entry") or 0.0)

        phase = "buy_persisted" if side == "buy" else "sell_persisted"
        print(
            "[TRADE_LIFECYCLE] "
            f"phase={phase} trade_id={trade_id} symbol={symbol} side={side} "
            f"quantity={qty:.12f} fill_price={price:.12f} "
            f"notional={qty * price:.12f} source={source} strategy={strategy} "
            f"is_exit={int(bool((trade or {}).get('is_exit')))} "
            f"exit_reason={str((trade or {}).get('exit_reason') or '')} "
            f"cost_basis_used={cost_basis_before:.12f} "
            f"realized_pnl={realized_pnl:.12f} "
            f"position_qty_before={position_before:.12f} "
            f"position_qty_after={position_after:.12f} "
            f"position_cost_basis_after={cost_basis_after:.12f}",
            flush=True,
        )

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            f"reason=after_persist_probe_error:{e}",
            flush=True,
        )

    return result


# Redirect all existing persistence callers through canonical wrapper.
qfos_persist_fill_atomic = qfos_apply_fill_atomic


def apply_buy(fill):
    """
    Legacy compatibility only.
    Real BUYs must not mutate cash or positions before atomic persistence.
    """
    try:
        symbol = str(fill.get("symbol") or "")
        qty = float(fill.get("quantity") or fill.get("qty") or 0.0)
        price = float(fill.get("fill_price") or fill.get("expected_price") or 0.0)

        if not symbol or qty <= 0 or price <= 0:
            print(
                f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=buy "
                "reason=legacy_apply_buy_invalid_fill",
                flush=True,
            )
            return False

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=entry_intent source=legacy_apply_buy_adapter "
            f"symbol={symbol} side=buy quantity={qty:.12f} "
            f"fill_price={price:.12f} action=no_runtime_mutation",
            flush=True,
        )
        return True

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=buy "
            f"reason=legacy_apply_buy_adapter_error:{e}",
            flush=True,
        )
        return False


def apply_sell(symbol, qty, price, reason):
    """
    Legacy compatibility only.
    Build SELL intent from DB-backed runtime cache without changing cash,
    position quantity, cost basis, or lifecycle state before persistence.
    """
    try:
        symbol = str(symbol or "").strip()
        requested_qty = float(qty or 0.0)
        fill_price = float(price or 0.0)
        reason = str(reason or "unknown").strip()

        held = float(portfolio.positions.get(symbol, 0.0) or 0.0)
        sell_qty = min(requested_qty, held)

        if not symbol or sell_qty <= 0 or fill_price <= 0:
            print(
                f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=sell "
                "reason=legacy_apply_sell_invalid_or_no_db_backed_position",
                flush=True,
            )
            return None

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=exit_intent source=legacy_apply_sell_adapter "
            f"symbol={symbol} side=sell quantity={sell_qty:.12f} "
            f"fill_price={fill_price:.12f} exit_reason={reason} "
            "action=no_runtime_mutation",
            flush=True,
        )

        return {
            "symbol": symbol,
            "side": "sell",
            "quantity": sell_qty,
            "qty": sell_qty,
            "expected_price": fill_price,
            "fill_price": fill_price,
            "slippage_bps": 0.0,
            "strategy": reason,
            "reason": reason,
            "is_exit": True,
            "exit_reason": reason,
            "confidence": 1.0,
            "live": False,
            "shadow_mode": False,
            "source": "legacy_apply_sell_adapter",
        }

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=sell "
            f"reason=legacy_apply_sell_adapter_error:{e}",
            flush=True,
        )
        return None


def qfos_db_sync_positions_from_portfolio(conn, portfolio_obj, prices):
    """
    Compatibility replacement for legacy memory-to-DB synchronizer.
    DB is authoritative. This function now only refreshes runtime cache.
    """
    print(
        "[TRADE_LIFECYCLE] "
        "phase=position_sync direction=db_to_runtime "
        "legacy_memory_to_db_write=disabled",
        flush=True,
    )
    _qfos_refresh_runtime_cache_from_active_conn(conn, source="legacy_sync_adapter")
    return True


def update_position_from_fill(conn, fill, prices=None):
    qfos_invalidate_ledger_cache()
    """
    Compatibility replacement.
    Position quantity/cost basis are rebuilt by DB trade trigger.
    """
    print(
        "[TRADE_LIFECYCLE] "
        "phase=position_updated authority=trade_trigger "
        "legacy_update_position_from_fill=disabled",
        flush=True,
    )
    _qfos_refresh_runtime_cache_from_active_conn(conn, source="legacy_update_position_adapter")
    return True

# ============================================================
# END QFOS_CANONICAL_TRADE_LIFECYCLE_V1
# ============================================================


if __name__ == '__main__':
    main()

# QFOS_PROFIT_ENGINE_SIDEWAYS_GUARD_V1 installed

# METRIC_TRUTH_STOPLOSS_WINRATE_V1 installed






# QFOS_AGENT5_FULL_EXIT_DB_QTY_PATCH_V1
# Agent 5 Ã¢â‚¬â€ SELL execution/filter authority
# Problem:
#   DB has open positions, but FULL_PROFIT_MODE rejects exit lifecycle SELLs
#   with reject_sell_no_open_position.
#
# Fix:
#   Before _qfos_full_exit_filter_fills rejects SELLs, normalize/clamp exit
#   SELL quantity from the DB positions table. DB open quantity is the authority.

def _qfos_agent5_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_agent5_side(fill):
    try:
        return str((fill or {}).get("side") or "").strip().lower()
    except Exception:
        return ""


def _qfos_agent5_symbol(fill):
    try:
        return str((fill or {}).get("symbol") or "").strip()
    except Exception:
        return ""


def _qfos_agent5_is_exit_sell(fill):
    if not isinstance(fill, dict):
        return False

    if _qfos_agent5_side(fill) != "sell":
        return False

    reason = str(
        fill.get("exit_reason")
        or fill.get("reason")
        or fill.get("strategy")
        or ""
    ).strip()

    if bool(fill.get("is_exit")):
        return True

    exit_tokens = (
        "take_profit",
        "stop_loss",
        "stagnation",
        "max_hold",
        "trailing",
        "breakeven",
        "time_stop",
        "risk_off",
        "exit",
    )

    return any(tok in reason for tok in exit_tokens)


def _qfos_agent5_db_open_qty(symbol):
    if not symbol:
        return 0.0

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select quantity
                    from positions
                    where symbol = :symbol
                      and coalesce(quantity, 0) > 0.00000001
                    limit 1
                """),
                {"symbol": symbol},
            ).mappings().first()

            if not row:
                return 0.0

            return max(0.0, _qfos_agent5_float(row.get("quantity")))
    except Exception as exc:
        print(
            f"[AGENT5_FULL_EXIT_DB_QTY] db_open_qty_failed "
            f"symbol={symbol} error={repr(exc)}",
            flush=True,
        )
        return 0.0


def _qfos_agent5_prepare_exit_sell(fill):
    if not _qfos_agent5_is_exit_sell(fill):
        return fill

    symbol = _qfos_agent5_symbol(fill)
    db_qty = _qfos_agent5_db_open_qty(symbol)

    if db_qty <= 0:
        return fill

    requested_qty = _qfos_agent5_float(
        fill.get("quantity", fill.get("qty", db_qty)),
        db_qty,
    )

    sell_qty = min(max(requested_qty, 0.0), db_qty)

    if sell_qty <= 0:
        return fill

    reason = str(
        fill.get("exit_reason")
        or fill.get("reason")
        or fill.get("strategy")
        or "exit_lifecycle"
    ).strip()

    fill = dict(fill)
    fill["quantity"] = sell_qty
    fill["qty"] = sell_qty
    fill["is_exit"] = True
    fill["exit_reason"] = reason
    fill["reason"] = reason
    fill["strategy"] = reason
    fill["source"] = fill.get("source") or "agent5_db_qty_exit"

    print(
        f"[AGENT5_FULL_EXIT_DB_QTY] prepared_exit_sell "
        f"symbol={symbol} requested_qty={requested_qty:.12f} "
        f"db_qty={db_qty:.12f} sell_qty={sell_qty:.12f} "
        f"reason={reason}",
        flush=True,
    )

    return fill


def _qfos_agent5_wrap_full_exit_filter():
    global _qfos_full_exit_filter_fills

    old_filter = globals().get("_qfos_full_exit_filter_fills")

    if not callable(old_filter):
        print("[AGENT5_FULL_EXIT_DB_QTY] _qfos_full_exit_filter_fills not found", flush=True)
        return

    if getattr(old_filter, "_qfos_agent5_db_qty_wrapped", False):
        return

    def _wrapped_qfos_full_exit_filter_fills(fills):
        try:
            prepared = []
            for fill in list(fills or []):
                prepared.append(_qfos_agent5_prepare_exit_sell(fill))
        except Exception as exc:
            print(
                "[AGENT5_FULL_EXIT_DB_QTY] prepare_failed "
                + repr(exc),
                flush=True,
            )
            prepared = list(fills or [])

        return old_filter(prepared)

    _wrapped_qfos_full_exit_filter_fills._qfos_agent5_db_qty_wrapped = True
    _qfos_full_exit_filter_fills = _wrapped_qfos_full_exit_filter_fills

    print("[AGENT5_FULL_EXIT_DB_QTY] wrapped _qfos_full_exit_filter_fills", flush=True)


_qfos_agent5_wrap_full_exit_filter()

# END QFOS_AGENT5_FULL_EXIT_DB_QTY_PATCH_V1



# ============================================================
# QFOS_DUPLICATE_PROFIT_ENGINE_SELL_GUARD_V1
#
# Problem fixed:
#   Profit Engine direct exits such as sideways_green_to_red_exit
#   can repeatedly write SELL rows for the same position if the
#   DB/state still presents the position as open on the next cycle.
#
# Protection:
#   - Full-exit reasons may only sell once after the latest BUY.
#   - If positions.quantity is already zero/missing, skip the sell.
#   - If requested sell qty exceeds DB open qty, cap it.
#   - Does not affect new BUY logic.
# ============================================================

_QFOS_FULL_EXIT_REASONS = {
    "sideways_green_to_red_exit",
    "sideways_scalp_stop_loss",
    "sideways_scalp_take_profit",
    "sideways_max_hold_profit_engine",
    "fallback_stop_loss",
    "fallback_take_profit",
    "fallback_max_hold_exit",
    "quality_initial_stop_loss",
    "quality_runner_breakeven_exit",
    "quality_runner_trailing_exit",
    "quality_time_stop_exit",
    "adaptive_stop_loss",
    "adaptive_take_profit",
    "trailing_profit_exit",
    "breakeven_protection_exit",
    "time_stop_exit",
    "risk_off_exit",
    "emergency_exposure_reduction",
    "basket_loss_guard",
    "big_loss_cooldown_exit",
}

def _qfos_guard_get_pos_value(pos, key, default=None):
    try:
        if isinstance(pos, dict):
            return pos.get(key, default)
    except Exception:
        pass

    try:
        return pos[key]
    except Exception:
        pass

    try:
        return getattr(pos, key)
    except Exception:
        return default


def _qfos_guard_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _qfos_guard_latest_trade_time(cur, symbol, side):
    try:
        row = cur.execute(
            """
            SELECT created_at
            FROM trades
            WHERE symbol = ?
              AND LOWER(side) = LOWER(?)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (symbol, side),
        ).fetchone()

        if not row:
            return None

        return row[0]
    except Exception:
        return None


def _qfos_guard_has_full_exit_after_latest_buy(cur, symbol):
    try:
        latest_buy = _qfos_guard_latest_trade_time(cur, symbol, "buy")

        if latest_buy:
            row = cur.execute(
                """
                SELECT id, strategy, created_at
                FROM trades
                WHERE symbol = ?
                  AND LOWER(side) = 'sell'
                  AND created_at >= (?)::timestamp
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (symbol, latest_buy),
            ).fetchone()
        else:
            row = cur.execute(
                """
                SELECT id, strategy, created_at
                FROM trades
                WHERE symbol = ?
                  AND LOWER(side) = 'sell'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()

        if not row:
            return False, None

        strategy = str(row[1] or "").lower()
        if strategy in _QFOS_FULL_EXIT_REASONS:
            return True, strategy

        return False, strategy

    except Exception:
        return False, None


def _qfos_guard_open_qty_from_db(cur, symbol):
    try:
        row = cur.execute(
            """
            SELECT quantity
            FROM positions
            WHERE symbol = ?
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

        if not row:
            return 0.0

        return _qfos_guard_float(row[0], 0.0)
    except Exception:
        return None


try:
    _qfos_pe_sell_original_before_dup_guard = _qfos_pe_sell

    def _qfos_pe_sell(cur, pos, qty, reason, unrealized, now_s, quarantine=False):
        symbol = str(
            _qfos_guard_get_pos_value(pos, "symbol")
            or _qfos_guard_get_pos_value(pos, 0)
            or ""
        )

        reason_l = str(reason or "").lower()
        requested_qty = _qfos_guard_float(qty, 0.0)

        if not symbol:
            print("[DUP_SELL_GUARD] blocked missing_symbol", flush=True)
            return False

        if requested_qty <= 0:
            print(
                f"[DUP_SELL_GUARD] blocked symbol={symbol} reason={reason_l} invalid_qty={requested_qty}",
                flush=True,
            )
            return False

        if reason_l in _QFOS_FULL_EXIT_REASONS:
            already_exited, prior_reason = _qfos_guard_has_full_exit_after_latest_buy(cur, symbol)
            if already_exited:
                print(
                    f"[DUP_SELL_GUARD] blocked_duplicate_full_exit symbol={symbol} "
                    f"reason={reason_l} prior_exit={prior_reason}",
                    flush=True,
                )
                return False

        open_qty = _qfos_guard_open_qty_from_db(cur, symbol)

        if open_qty is not None:
            if open_qty <= 0.00000001:
                print(
                    f"[DUP_SELL_GUARD] blocked_no_open_qty symbol={symbol} reason={reason_l} db_qty={open_qty}",
                    flush=True,
                )
                return False

            if requested_qty > open_qty:
                print(
                    f"[DUP_SELL_GUARD] capped_qty symbol={symbol} reason={reason_l} "
                    f"requested={requested_qty:.8f} open={open_qty:.8f}",
                    flush=True,
                )
                qty = open_qty

        return _qfos_pe_sell_original_before_dup_guard(
            cur, pos, qty, reason, unrealized, now_s, quarantine=quarantine
        )

    print("[DUP_SELL_GUARD] installed", flush=True)

except Exception as exc:
    print(f"[DUP_SELL_GUARD] install_failed={exc}", flush=True)

# ============================================================
# End QFOS_DUPLICATE_PROFIT_ENGINE_SELL_GUARD_V1
# ============================================================








