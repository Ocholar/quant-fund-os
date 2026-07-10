"""Truthful execution-cycle telemetry.

This module separates fills that were merely proposed from fills that were
actually persisted through the atomic trading boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class RejectedFill:
    symbol: str
    side: str
    reason: str


@dataclass
class ExecutionCycleTelemetry:
    raw_orders: int = 0
    proposed_fills: int = 0
    persisted_fills: int = 0
    rejected_fills: int = 0
    rejected: List[RejectedFill] = field(default_factory=list)

    @property
    def final_applied_fills(self) -> int:
        """Backward-compatible field: only persisted fills count as applied."""
        return self.persisted_fills

    def record_persistence_result(
        self,
        fill: Dict[str, Any] | None,
        persistence_result: Any,
        reject_reason: str = "atomic_persistence_rejected",
    ) -> bool:
        """Record one attempted fill.

        The legacy atomic boundary returns None in audit mode and on rejected
        persistence. Only a non-None, non-False result is counted as persisted.
        """
        if persistence_result is None or persistence_result is False:
            self.rejected_fills += 1
            payload = fill if isinstance(fill, dict) else {}
            self.rejected.append(
                RejectedFill(
                    symbol=str(payload.get("symbol") or "UNKNOWN"),
                    side=str(payload.get("side") or "UNKNOWN").lower(),
                    reason=str(reject_reason or "atomic_persistence_rejected"),
                )
            )
            return False

        self.persisted_fills += 1
        return True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_orders": int(self.raw_orders),
            "proposed_fills": int(self.proposed_fills),
            "persisted_fills": int(self.persisted_fills),
            "rejected_fills": int(self.rejected_fills),
            "final_applied_fills": int(self.final_applied_fills),
            "rejected_fill_details": [
                {
                    "symbol": item.symbol,
                    "side": item.side,
                    "reason": item.reason,
                }
                for item in self.rejected
            ],
        }
