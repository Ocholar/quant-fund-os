from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

old_pattern = re.compile(
    r"""    try:\n        current_drawdown = float\(getattr\(portfolio,\s*'drawdown',\s*0\.0\) or 0\.0\)\n        caution_drawdown = float\(getattr\(settings,\s*'caution_drawdown',\s*-0\.02\)\)\n        blocked_drawdown = float\(getattr\(settings,\s*'blocked_drawdown',\s*-0\.05\)\)\n        if current_drawdown <= blocked_drawdown \* 0\.9:\n            return \(False,\s*f'near_blocked_drawdown_\{current_drawdown:\.4f\}'\)\n        if current_drawdown <= caution_drawdown:\n            open_positions_count = sum\(\(1 for _, q in portfolio\.positions\.items\(\) if float\(q or 0\) > 1e-08\)\)\n            try:\n                current_exposure = float\(getattr\(portfolio,\s*'exposure',\s*0\.0\) or 0\.0\)\n            except Exception:\n                current_exposure = 0\.0\n            try:\n                exposure_pct = current_exposure / max\(float\(equity or 0\.0\),\s*1e-09\)\n            except Exception:\n                exposure_pct = 0\.0\n            if open_positions_count >= 2:\n                return \(False,\s*f'caution_drawdown_position_cap_\{current_drawdown:\.4f\}'\)\n            if exposure_pct >= 0\.5:\n                return \(False,\s*f'caution_drawdown_exposure_\{current_drawdown:\.4f\}_\{exposure_pct:\.4f\}'\)\n    except Exception:\n        pass\n""",
    re.MULTILINE,
)

new_block = """    try:
        current_drawdown = float(getattr(portfolio, 'drawdown', 0.0) or 0.0)
        caution_drawdown = float(getattr(settings, 'caution_drawdown', -0.02))
        blocked_drawdown = float(getattr(settings, 'blocked_drawdown', -0.05))
        near_buffer = abs(float(getattr(settings, 'near_blocked_drawdown_buffer', 0.0025)))
        near_blocked_drawdown = blocked_drawdown + near_buffer

        open_positions_count = sum(
            1 for _, q in portfolio.positions.items()
            if float(q or 0) > 1e-08
        )

        # Phase 3A stale drawdown repair:
        # If DB/runtime is clean and the equity argument says reset baseline,
        # do not let an old portfolio.peak/equity memory block fresh BUYs.
        if open_positions_count == 0 and float(equity or 0.0) >= INITIAL_EQUITY * 0.999:
            if current_drawdown < 0:
                try:
                    portfolio.cash = float(equity or INITIAL_EQUITY)
                    portfolio.equity = float(equity or INITIAL_EQUITY)
                    portfolio.peak = max(float(INITIAL_EQUITY), float(equity or INITIAL_EQUITY))
                    current_drawdown = 0.0
                    print('[AGENT2_RISK_RESET] cleared stale drawdown gate in can_buy', flush=True)
                except Exception:
                    pass

        # Hard blocked must be evaluated before near-blocked.
        # A true breach should say blocked_drawdown, not near_blocked_drawdown.
        if current_drawdown <= blocked_drawdown:
            return (False, f'blocked_drawdown_{current_drawdown:.4f}')

        # Near-blocked is only the warning zone before hard blocked.
        if current_drawdown <= near_blocked_drawdown:
            return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')

        if current_drawdown <= caution_drawdown:
            try:
                current_exposure = float(getattr(portfolio, 'exposure', 0.0) or 0.0)
            except Exception:
                current_exposure = 0.0
            try:
                exposure_pct = current_exposure / max(float(equity or 0.0), 1e-09)
            except Exception:
                exposure_pct = 0.0
            if open_positions_count >= 2:
                return (False, f'caution_drawdown_position_cap_{current_drawdown:.4f}')
            if exposure_pct >= 0.5:
                return (False, f'caution_drawdown_exposure_{current_drawdown:.4f}_{exposure_pct:.4f}')
    except Exception:
        pass
"""

new_text, count = old_pattern.subn(new_block, text, count=1)

if count != 1:
    raise SystemExit("MAIN_CAN_BUY_PATCH_FAILED: expected stale drawdown block not found exactly once")

path.write_text(new_text, encoding="utf-8")
print("MAIN_CAN_BUY_PATCH_OK")
