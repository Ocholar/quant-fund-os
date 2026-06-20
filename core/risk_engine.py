from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import settings


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    risk_status: str


class RiskEngine:
    """
    Percentage-based risk engine.

    Rules:
    - Risk status must be derived from current portfolio state.
    - Clean reset must clear stale peak/drawdown/pause memory.
    - SAFE is impossible when drawdown or exposure violates configured limits.
    - BLOCKED must be evaluated before NEAR_BLOCKED.
    """

    def __init__(self):
        self.reset_risk_state(settings.starting_equity)

    def reset_risk_state(self, starting_equity: float | None = None) -> None:
        base = float(settings.starting_equity if starting_equity is None else starting_equity)

        self.max_daily_loss = float(settings.max_daily_loss)
        self.max_drawdown = float(settings.max_portfolio_drawdown)
        self.max_leverage = float(settings.max_leverage)

        self.starting_equity = base
        self.peak_equity = base
        self.paused = False
        self.pause_reason = ""
        self.last_risk_status = "SAFE"

    def tune(self, regime: str):
        self.max_daily_loss = float(settings.max_daily_loss)
        self.max_drawdown = float(settings.max_portfolio_drawdown)
        self.max_leverage = float(settings.max_leverage)

        if regime == "RISK_OFF":
            self.max_daily_loss = min(self.max_daily_loss, 0.005)
            self.max_drawdown = min(self.max_drawdown, 0.025)
            self.max_leverage = min(self.max_leverage, 0.25)
        elif regime == "TREND":
            self.max_leverage = float(settings.max_leverage)

    def drawdown_from_equity(self, equity: float, peak_equity: float | None = None) -> float:
        peak = float(self.peak_equity if peak_equity is None else peak_equity)

        if peak <= 0:
            return 0.0

        return (float(equity) - peak) / peak

    def risk_status(
        self,
        *,
        equity: float,
        exposure_pct: float,
        peak_equity: float | None = None,
    ) -> str:
        equity = float(equity)
        exposure_pct = max(0.0, float(exposure_pct or 0.0))

        peak = float(self.peak_equity if peak_equity is None else peak_equity)

        if equity > peak:
            peak = equity
            self.peak_equity = equity

        drawdown = self.drawdown_from_equity(equity, peak)

        if drawdown <= float(settings.blocked_drawdown):
            self.last_risk_status = "BLOCKED"
        elif exposure_pct >= float(settings.max_total_exposure_pct):
            self.last_risk_status = "BLOCKED"
        elif drawdown <= float(settings.caution_drawdown):
            self.last_risk_status = "CAUTION"
        elif exposure_pct >= float(settings.caution_exposure_pct):
            self.last_risk_status = "CAUTION"
        else:
            self.last_risk_status = "SAFE"

        return self.last_risk_status

    def can_buy(
        self,
        *,
        equity: float,
        cash: float,
        exposure_pct: float,
        allocation_notional: float = 0.0,
        peak_equity: float | None = None,
    ) -> RiskDecision:
        equity = float(equity)
        cash = float(cash)
        exposure_pct = max(0.0, float(exposure_pct or 0.0))
        allocation_notional = max(0.0, float(allocation_notional or 0.0))

        peak = float(self.peak_equity if peak_equity is None else peak_equity)

        if equity > peak:
            peak = equity
            self.peak_equity = equity

        drawdown = self.drawdown_from_equity(equity, peak)
        current_status = self.risk_status(
            equity=equity,
            exposure_pct=exposure_pct,
            peak_equity=peak,
        )

        if self.paused:
            return RiskDecision(False, self.pause_reason or "paused", current_status)

        if cash <= 0:
            return RiskDecision(False, "no_cash", current_status)

        if allocation_notional > cash:
            return RiskDecision(False, "insufficient_cash", current_status)

        # Hard block must come before near-block.
        # A true -5%/-6% drawdown is BLOCKED, not near-blocked.
        if drawdown <= float(settings.blocked_drawdown):
            return RiskDecision(
                False,
                f"blocked_drawdown_{drawdown:.4f}",
                "BLOCKED",
            )

        # Near-block is only the warning zone before the hard blocked line.
        if drawdown <= float(settings.near_blocked_drawdown):
            return RiskDecision(
                False,
                f"near_blocked_drawdown_{drawdown:.4f}",
                current_status,
            )

        if current_status == "BLOCKED":
            return RiskDecision(False, "risk_status_BLOCKED", current_status)

        projected_exposure_pct = exposure_pct

        if equity > 0 and allocation_notional > 0:
            projected_exposure_pct = ((exposure_pct * equity) + allocation_notional) / equity

        if projected_exposure_pct > float(settings.max_total_exposure_pct):
            return RiskDecision(
                False,
                f"max_total_exposure_pct_{projected_exposure_pct:.4f}",
                current_status,
            )

        return RiskDecision(True, "approved", current_status)

    def approve(self, allocation: dict[str, Any]) -> dict[str, Any] | None:
        if settings.require_human_approval and settings.live_trading:
            return None

        if float(allocation.get("leverage", 0) or 0) > self.max_leverage:
            return None

        if float(allocation.get("estimated_var", 0) or 0) > self.max_daily_loss:
            return None

        return allocation



# ============================================================
# QFOS_AGENT2_CLEAN_LEDGER_RISK_SAFE_V1
# Purpose:
#   A clean ledger baseline must not produce BLOCKED/max_daily_loss
#   without real trades or open positions proving loss.
# ============================================================

def qfos_clean_ledger_forces_safe(trades_count=0, open_position_count=0):
    try:
        return int(trades_count or 0) == 0 and int(open_position_count or 0) == 0
    except Exception:
        return False


def qfos_clean_ledger_safe_status(default_status="SAFE", trades_count=0, open_position_count=0):
    if qfos_clean_ledger_forces_safe(trades_count, open_position_count):
        return "SAFE"
    return default_status


def qfos_clean_ledger_blocks_stale_max_daily_loss(reason="", trades_count=0, open_position_count=0):
    if not qfos_clean_ledger_forces_safe(trades_count, open_position_count):
        return False
    r = str(reason or "").lower()
    return (
        "max_daily_loss_hit" in r
        or "near_blocked_drawdown" in r
        or "blocked_drawdown" in r
        or "blocked" in r
    )

# ============================================================
# End QFOS_AGENT2_CLEAN_LEDGER_RISK_SAFE_V1
# ============================================================

