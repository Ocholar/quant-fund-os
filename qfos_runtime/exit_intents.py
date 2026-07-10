"""Canonical exit-intent representation and de-duplication.

This module does not persist trades. It creates one normalized intent list
for the eventual single execution owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class ExitIntent:
    symbol: str
    side: str
    quantity: float
    fill_price: float
    reason: str
    source: str
    is_exit: bool = True

    @property
    def key(self) -> Tuple[str, str]:
        return (self.symbol.upper(), self.side.lower())

    def as_fill(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "expected_price": self.fill_price,
            "fill_price": self.fill_price,
            "price": self.fill_price,
            "strategy": self.reason,
            "reason": self.reason,
            "exit_reason": self.reason,
            "source": self.source,
            "is_exit": self.is_exit,
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def from_fill(fill: Dict[str, Any], default_source: str = "unknown") -> ExitIntent | None:
    if not isinstance(fill, dict):
        return None

    symbol = _text(fill.get("symbol"))
    side = _text(fill.get("side")).lower()
    quantity = _number(fill.get("quantity"))
    fill_price = _number(
        fill.get("fill_price")
        or fill.get("expected_price")
        or fill.get("price")
    )
    reason = _text(
        fill.get("exit_reason")
        or fill.get("reason")
        or fill.get("strategy")
    )
    source = _text(fill.get("source")) or default_source

    if not symbol or side != "sell" or quantity <= 0 or fill_price <= 0:
        return None

    return ExitIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        fill_price=fill_price,
        reason=reason or "unknown_exit",
        source=source,
        is_exit=True,
    )


def deduplicate_exit_intents(
    fills: Iterable[Dict[str, Any]],
    default_source: str = "unknown",
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return canonical fills and duplicate-rejection records.

    First valid intent for a symbol/side wins. This deliberately makes no
    trading decision and does not alter sizing or price.
    """
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()

    for fill in fills:
        intent = from_fill(fill, default_source=default_source)
        if intent is None:
            continue

        if intent.key in seen:
            rejected.append(
                {
                    "symbol": intent.symbol,
                    "reason": "duplicate_exit_intent",
                    "source": intent.source,
                    "side": intent.side,
                }
            )
            continue

        seen.add(intent.key)
        accepted.append(intent.as_fill())

    return accepted, rejected
