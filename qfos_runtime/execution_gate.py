"""Final control-state gate for fill persistence.

The gate makes no trading decision. It only prevents a fill from reaching
atomic persistence when the shared control state is paused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


ControlStateReader = Callable[[], Dict[str, Any]]


@dataclass(frozen=True)
class ExecutionGateDecision:
    allowed: bool
    reason: str
    paused: bool
    pause_reason: str


def _normalize_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "available": False,
            "paused": True,
            "reason": "control_state_unavailable",
        }

    if "paused" not in value:
        return {
            "available": False,
            "paused": True,
            "reason": "control_state_unavailable",
        }

    return {
        "available": True,
        "paused": bool(value.get("paused")),
        "reason": str(value.get("reason") or ""),
    }


def _fill_side(fill: Any) -> str:
    try:
        if isinstance(fill, dict):
            return str(fill.get("side") or "").lower().strip()
        return str(getattr(fill, "side", "") or "").lower().strip()
    except Exception:
        return ""


def evaluate_execution_gate(
    state_reader: ControlStateReader,
    fill: Any = None,
) -> ExecutionGateDecision:
    state = _normalize_state(state_reader())
    side = _fill_side(fill)

    if not state["available"]:
        return ExecutionGateDecision(
            allowed=False,
            reason="control_state_unavailable",
            paused=True,
            pause_reason="control_state_unavailable",
        )

    if state["paused"]:
        reason = state["reason"] or "manual_pause"

        # Pause and kill-switch prohibit new exposure. Existing spot positions
        # must still be able to exit through atomic sell validation.
        if side == "sell":
            return ExecutionGateDecision(
                allowed=True,
                reason=f"control_paused_exit_allowed:{reason}",
                paused=True,
                pause_reason=reason,
            )

        return ExecutionGateDecision(
            allowed=False,
            reason=f"control_paused_entry_blocked:{reason}",
            paused=True,
            pause_reason=reason,
        )

    return ExecutionGateDecision(
        allowed=True,
        reason="control_running",
        paused=False,
        pause_reason="",
    )


def default_execution_gate() -> Callable[[Any], ExecutionGateDecision]:
    from core.control import get_control_state

    return lambda fill=None: evaluate_execution_gate(get_control_state, fill)
