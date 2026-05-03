from core.config import settings

class RiskEngine:
    def __init__(self):
        self.max_daily_loss = settings.max_daily_loss
        self.max_drawdown = settings.max_portfolio_drawdown
        self.max_leverage = settings.max_leverage

    def tune(self, regime: str):
        if regime == "RISK_OFF":
            self.max_daily_loss = min(self.max_daily_loss, 0.005)
            self.max_drawdown = min(self.max_drawdown, 0.025)
            self.max_leverage = min(self.max_leverage, 0.25)
        elif regime == "TREND":
            self.max_leverage = settings.max_leverage

    def approve(self, allocation: dict) -> dict | None:
        if settings.require_human_approval and settings.live_trading:
            return None
        if allocation.get("leverage", 0) > self.max_leverage:
            return None
        if allocation.get("estimated_var", 0) > self.max_daily_loss:
            return None
        return allocation
