from pathlib import Path

path = Path("core/risk_engine.py")
text = path.read_text(encoding="utf-8")

if "def reset_risk_state" not in text:
    append = r'''

# QFOS_BASELINE_AUTHORITY_RISK_RESET_START
def _qfos_risk_engine_reset_risk_state(self, starting_equity=None):
    from core.config import settings
    base = float(settings.starting_equity if starting_equity is None else starting_equity)
    self.max_daily_loss = float(settings.max_daily_loss)
    self.max_drawdown = float(settings.max_portfolio_drawdown)
    self.max_leverage = float(settings.max_leverage)
    self.starting_equity = base
    self.peak_equity = base
    self.paused = False
    self.pause_reason = ""
    self.last_risk_status = "SAFE"

try:
    RiskEngine.reset_risk_state = _qfos_risk_engine_reset_risk_state
except Exception:
    pass
# QFOS_BASELINE_AUTHORITY_RISK_RESET_END
'''
    text = text + append
else:
    # No destructive rewrite here. The previous Agent 2 patch already added this.
    pass

path.write_text(text, encoding="utf-8")
print("RISK_ENGINE_BASELINE_PATCH_OK")
