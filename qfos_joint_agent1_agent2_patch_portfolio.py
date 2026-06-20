from pathlib import Path
import re

path = Path("core/portfolio.py")
text = path.read_text(encoding="utf-8")

if "def reset(" not in text:
    # Insert reset method inside Portfolio class after __init__ if present.
    reset_method = '''
    def reset(self, starting_equity=100.0):
        base = float(starting_equity)
        self.cash = base
        self.equity = base
        self.peak = base
        if hasattr(self, "positions"):
            self.positions.clear()
        else:
            self.positions = {}
        if hasattr(self, "avg_entry"):
            self.avg_entry.clear()
        if hasattr(self, "realized_pnl"):
            self.realized_pnl = 0.0
        if hasattr(self, "unrealized_pnl"):
            self.unrealized_pnl = 0.0
'''

    lines = text.splitlines()
    out = []
    inserted = False

    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.strip().startswith("def __init__"):
            # Insert after the __init__ block by waiting for next method.
            continue

    # Safer fallback: append monkey patch if class edit is risky.
    text += '''

# QFOS_BASELINE_AUTHORITY_PORTFOLIO_RESET_START
def _qfos_portfolio_reset(self, starting_equity=100.0):
    base = float(starting_equity)
    self.cash = base
    self.equity = base
    self.peak = base
    if hasattr(self, "positions"):
        self.positions.clear()
    else:
        self.positions = {}
    if hasattr(self, "avg_entry"):
        self.avg_entry.clear()
    if hasattr(self, "realized_pnl"):
        self.realized_pnl = 0.0
    if hasattr(self, "unrealized_pnl"):
        self.unrealized_pnl = 0.0

try:
    Portfolio.reset = _qfos_portfolio_reset
except Exception:
    pass
# QFOS_BASELINE_AUTHORITY_PORTFOLIO_RESET_END
'''
else:
    pass

path.write_text(text, encoding="utf-8")
print("PORTFOLIO_BASELINE_PATCH_OK")
