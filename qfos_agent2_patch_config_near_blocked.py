from pathlib import Path

path = Path("core/config.py")
text = path.read_text(encoding="utf-8")

old = '''    @property
    def near_blocked_drawdown(self) -> float:
        """
        Drawdown threshold where new BUYs should stop before hard BLOCKED.

        Important:
        This must only be applied to CURRENT portfolio state.
        It must not be applied from stale DB snapshots, stale pause state,
        stale state files, or old runtime memory after clean reset.
        """
        return float(self.blocked_drawdown) - abs(float(self.near_blocked_drawdown_buffer))
'''

new = '''    @property
    def near_blocked_drawdown(self) -> float:
        """
        Drawdown threshold where new BUYs should stop before hard BLOCKED.

        Drawdown is negative. Therefore the near-blocked threshold must be
        LESS negative than the hard blocked threshold.

        Example:
            blocked_drawdown = -0.0500
            near_blocked_drawdown_buffer = 0.0025
            near_blocked_drawdown = -0.0475

        Important:
        This must only be applied to CURRENT portfolio state.
        It must not be applied from stale DB snapshots, stale pause state,
        stale state files, or old runtime memory after clean reset.
        """
        return float(self.blocked_drawdown) + abs(float(self.near_blocked_drawdown_buffer))
'''

if old not in text:
    raise SystemExit("Could not find old near_blocked_drawdown property in core/config.py")

path.write_text(text.replace(old, new), encoding="utf-8")
print("CONFIG_NEAR_BLOCKED_PATCH_OK")
