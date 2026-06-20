"""
core.exit_lifecycle

Joint Agent 2 + Agent 5 exit lifecycle policy.

Scope:
- Agent 2: exit decision rules and logging
- Agent 5: SELL order shape required for safe execution/accounting

This module does NOT mutate cash, positions, accounting, entry allocation,
feature generation, or live trading settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ExitPolicy:
    take_profit_pct: float = 0.012          # +1.20%
    stop_loss_pct: float = -0.008           # -0.80%
    sideways_take_profit_pct: float = 0.006 # +0.60%
    sideways_stop_loss_pct: float = -0.006  # -0.60%

    sideways_stagnation_age_min: float = 20.0
    sideways_stagnation_low_pct: float = -0.0025   # -0.25%
    sideways_stagnation_high_pct: float = 0.0035   # +0.35%

    max_hold_age_min: float = 45.0

    trailing_peak_pct: float = 0.0045       # +0.45%
    trailing_floor_pct: float = 0.0015      # +0.15%

    breakeven_peak_pct: float = 0.0035      # +0.35%
    breakeven_floor_pct: float = 0.0002     # +0.02%


_POLICY = ExitPolicy()
_PEAK_PNL_BY_SYMBOL: Dict[str, float] = {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _pos_get(position: Any, key: str, default: Any = None) -> Any:
    if isinstance(position, dict):
        return position.get(key, default)
    return getattr(position, key, default)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def _position_age_minutes(position: Any, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)

    for key in (
        "opened_at",
        "created_at",
        "first_buy_at",
        "entry_time",
        "timestamp",
        "updated_at",
    ):
        dt = _parse_dt(_pos_get(position, key))
        if dt is not None:
            return max(0.0, (now - dt).total_seconds() / 60.0)

    return 0.0


def _position_price(position: Any) -> Tuple[float, float]:
    avg_entry = _to_float(
        _pos_get(position, "avg_entry",
        _pos_get(position, "entry_price",
        _pos_get(position, "average_price", 0.0)))
    )

    mark_price = _to_float(
        _pos_get(position, "mark_price",
        _pos_get(position, "last_price",
        _pos_get(position, "price", avg_entry)))
    )

    return avg_entry, mark_price


def _position_qty(position: Any) -> float:
    return _to_float(
        _pos_get(position, "quantity",
        _pos_get(position, "qty",
        _pos_get(position, "size", 0.0)))
    )


def _is_sideways(regime: Any) -> bool:
    return str(regime or "").upper() == "SIDEWAYS"


def _runner_conditions_true(position: Any, pnl_pct: float, peak_pct: float, regime: Any) -> bool:
    """
    Conservative runner allowance.

    We only suppress max-hold/stagnation exits if the position is clearly green.
    This avoids using vague trend flags to trap capital in flat positions.
    """
    symbol_regime = str(_pos_get(position, "symbol_regime", "") or "").upper()
    is_uptrend = bool(_pos_get(position, "is_symbol_uptrend", False))
    breakout_up = bool(_pos_get(position, "breakout_up", False))

    if pnl_pct >= 0.006 and peak_pct >= 0.006:
        return True

    if pnl_pct >= 0.004 and ("TREND_UP" in symbol_regime or is_uptrend or breakout_up):
        return True

    return False


def evaluate_exit_decision(
    position: Any,
    regime: Any = None,
    now: Optional[datetime] = None,
    policy: ExitPolicy = _POLICY,
) -> Dict[str, Any]:
    """
    Evaluate one open position and return a structured HOLD/SELL decision.

    Return keys:
    - symbol
    - decision: HOLD or SELL
    - reason
    - age_min
    - pnl_pct
    - peak_pnl_pct
    - quantity
    - avg_entry
    - mark_price
    """

    now = now or datetime.now(timezone.utc)

    symbol = str(_pos_get(position, "symbol", "") or "").strip()
    quantity = _position_qty(position)
    avg_entry, mark_price = _position_price(position)
    age_min = _position_age_minutes(position, now=now)

    if not symbol:
        return {
            "symbol": "",
            "decision": "HOLD",
            "reason": "hold_missing_symbol",
            "age_min": age_min,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    if quantity <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_no_open_quantity",
            "age_min": age_min,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    if avg_entry <= 0 or mark_price <= 0:
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_invalid_price",
            "age_min": age_min,
            "pnl_pct": 0.0,
            "peak_pnl_pct": 0.0,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    pnl_pct = (mark_price - avg_entry) / avg_entry
    old_peak = _PEAK_PNL_BY_SYMBOL.get(symbol, pnl_pct)
    peak_pnl_pct = max(old_peak, pnl_pct)
    _PEAK_PNL_BY_SYMBOL[symbol] = peak_pnl_pct

    sideways = _is_sideways(regime)
    runner = _runner_conditions_true(position, pnl_pct, peak_pnl_pct, regime)

    # 1. Take profit
    tp = policy.sideways_take_profit_pct if sideways else policy.take_profit_pct
    if pnl_pct >= tp:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_take_profit_exit" if sideways else "take_profit_exit",
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    # 2. Stop loss
    sl = policy.sideways_stop_loss_pct if sideways else policy.stop_loss_pct
    if pnl_pct <= sl:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "sideways_stop_loss_exit" if sideways else "stop_loss_exit",
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    # 5. Profit protection / trailing exit
    if peak_pnl_pct >= policy.trailing_peak_pct and pnl_pct <= policy.trailing_floor_pct:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "trailing_profit_exit",
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    # 6. Breakeven protection
    if peak_pnl_pct >= policy.breakeven_peak_pct and pnl_pct <= policy.breakeven_floor_pct:
        return {
            "symbol": symbol,
            "decision": "SELL",
            "reason": "breakeven_protection_exit",
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    # 3. Sideways stagnation exit
    if sideways and age_min >= policy.sideways_stagnation_age_min:
        if policy.sideways_stagnation_low_pct <= pnl_pct <= policy.sideways_stagnation_high_pct:
            if not runner:
                return {
                    "symbol": symbol,
                    "decision": "SELL",
                    "reason": "sideways_stagnation_exit",
                    "age_min": age_min,
                    "pnl_pct": pnl_pct,
                    "peak_pnl_pct": peak_pnl_pct,
                    "quantity": quantity,
                    "avg_entry": avg_entry,
                    "mark_price": mark_price,
                }
            return {
                "symbol": symbol,
                "decision": "HOLD",
                "reason": "hold_runner_conditions_true",
                "age_min": age_min,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "quantity": quantity,
                "avg_entry": avg_entry,
                "mark_price": mark_price,
            }

    # 4. Max hold exit
    if age_min >= policy.max_hold_age_min:
        if not runner:
            return {
                "symbol": symbol,
                "decision": "SELL",
                "reason": "sideways_max_hold_exit" if sideways else "max_hold_exit",
                "age_min": age_min,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "quantity": quantity,
                "avg_entry": avg_entry,
                "mark_price": mark_price,
            }
        return {
            "symbol": symbol,
            "decision": "HOLD",
            "reason": "hold_runner_conditions_true",
            "age_min": age_min,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": peak_pnl_pct,
            "quantity": quantity,
            "avg_entry": avg_entry,
            "mark_price": mark_price,
        }

    hold_reasons = []

    if age_min < policy.sideways_stagnation_age_min:
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
        "age_min": age_min,
        "pnl_pct": pnl_pct,
        "peak_pnl_pct": peak_pnl_pct,
        "quantity": quantity,
        "avg_entry": avg_entry,
        "mark_price": mark_price,
    }


def log_exit_decision(decision: Dict[str, Any], logger: Any = None) -> None:
    line = (
        "[EXIT_DECISION] "
        f"symbol={decision.get('symbol')} "
        f"age_min={float(decision.get('age_min', 0.0)):.2f} "
        f"pnl_pct={float(decision.get('pnl_pct', 0.0)):.4%} "
        f"peak_pnl_pct={float(decision.get('peak_pnl_pct', 0.0)):.4%} "
        f"decision={decision.get('decision')} "
        f"reason={decision.get('reason')}"
    )

    if logger is not None:
        try:
            logger.info(line)
            return
        except Exception:
            pass

    print(line, flush=True)


def build_exit_order(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build a SELL order shape for the existing execution layer.

    The execution/accounting layer must still validate:
    - quantity <= open quantity
    - is_exit=true
    - exit_reason is not null
    - duplicate full exit prevention
    """

    if decision.get("decision") != "SELL":
        return None

    symbol = str(decision.get("symbol") or "").strip()
    quantity = _to_float(decision.get("quantity"), 0.0)
    price = _to_float(decision.get("mark_price"), 0.0)
    reason = str(decision.get("reason") or "").strip()

    if not symbol or quantity <= 0 or price <= 0 or not reason:
        return None

    return {
        "symbol": symbol,
        "side": "SELL",
        "quantity": quantity,
        "price": price,
        "is_exit": True,
        "exit_reason": reason,
        "source": "exit_lifecycle",
        "strategy": "exit_lifecycle",
    }


def evaluate_open_positions_for_exits(
    positions: Any,
    regime: Any = None,
    logger: Any = None,
) -> list:
    """
    Evaluate all open positions and return SELL exit orders.
    Logs one EXIT_DECISION per position every cycle.
    """

    if positions is None:
        return []

    if isinstance(positions, dict):
        iterable = list(positions.values())
    else:
        iterable = list(positions)

    exit_orders = []

    for position in iterable:
        decision = evaluate_exit_decision(position, regime=regime)
        log_exit_decision(decision, logger=logger)

        order = build_exit_order(decision)
        if order is not None:
            exit_orders.append(order)

    return exit_orders
