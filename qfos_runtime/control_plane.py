"""Single control-plane adapter for pause, resume, and state reads.

This adapter does not own Redis. core.control remains the storage authority.
It gives API and runtime callers one normalized command/read contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


StateReader = Callable[[], Dict[str, Any]]
PauseWriter = Callable[[str], None]
ResumeWriter = Callable[[], None]


@dataclass
class ControlPlane:
    pause_writer: PauseWriter
    resume_writer: ResumeWriter
    state_reader: StateReader

    @staticmethod
    def normalize_state(value: Any) -> Dict[str, Any]:
        data = value if isinstance(value, dict) else {}
        return {
            "paused": bool(data.get("paused", False)),
            "reason": str(data.get("reason") or ""),
            "updated_at": data.get("updated_at"),
        }

    def state(self) -> Dict[str, Any]:
        return self.normalize_state(self.state_reader())

    def pause(self, reason: str = "manual_pause") -> Dict[str, Any]:
        normalized_reason = str(reason or "").strip() or "manual_pause"
        self.pause_writer(normalized_reason)
        return self.state()

    def resume(self) -> Dict[str, Any]:
        self.resume_writer()
        return self.state()


def default_control_plane() -> ControlPlane:
    from core.control import get_control_state, pause_bot, resume_bot

    return ControlPlane(
        pause_writer=pause_bot,
        resume_writer=resume_bot,
        state_reader=get_control_state,
    )
