from pathlib import Path

path = Path("main.py")
lines = path.read_text(encoding="utf-8").splitlines()

start = None
end = None

for i, line in enumerate(lines):
    if "current_drawdown = float(getattr(portfolio, 'drawdown', 0.0) or 0.0)" in line:
        start = i
        break

if start is None:
    raise SystemExit("PATCH_FAILED: could not find current_drawdown block in main.py")

# Find the end of that try/except block.
# We stop at the first:
#     except Exception:
#         pass
# after current_drawdown block.
for j in range(start, min(start + 80, len(lines) - 1)):
    if lines[j].strip() == "except Exception:" and lines[j + 1].strip() == "pass":
        end = j + 2
        break

if end is None:
    raise SystemExit("PATCH_FAILED: could not find end of current_drawdown try/except block")

old_block = "\n".join(lines[start:end])

if "blocked_drawdown * 0.9" not in old_block:
    raise SystemExit("PATCH_FAILED: target block found, but blocked_drawdown * 0.9 was not inside it")

new_block = [
"    try:",
"        current_drawdown = float(getattr(portfolio, 'drawdown', 0.0) or 0.0)",
"        caution_drawdown = float(getattr(settings, 'caution_drawdown', -0.02))",
"        blocked_drawdown = float(getattr(settings, 'blocked_drawdown', -0.05))",
"        near_buffer = abs(float(getattr(settings, 'near_blocked_drawdown_buffer', 0.0025)))",
"        near_blocked_drawdown = blocked_drawdown + near_buffer",
"",
"        open_positions_count = sum(",
"            1 for _, q in portfolio.positions.items()",
"            if float(q or 0) > 1e-08",
"        )",
"",
"        # Agent 2 Phase 3A stale drawdown repair:",
"        # If runtime/DB is clean and equity is back at the reset baseline,",
"        # do not let old portfolio.peak/equity memory block fresh BUYs.",
"        if open_positions_count == 0 and float(equity or 0.0) >= INITIAL_EQUITY * 0.999:",
"            if current_drawdown < 0:",
"                try:",
"                    portfolio.cash = float(equity or INITIAL_EQUITY)",
"                    portfolio.equity = float(equity or INITIAL_EQUITY)",
"                    portfolio.peak = max(float(INITIAL_EQUITY), float(equity or INITIAL_EQUITY))",
"                    current_drawdown = 0.0",
"                    print('[AGENT2_RISK_RESET] cleared stale drawdown gate in can_buy', flush=True)",
"                except Exception:",
"                    pass",
"",
"        # Hard blocked must come before near-blocked.",
"        # A real hard breach should not be mislabeled near_blocked_drawdown.",
"        if current_drawdown <= blocked_drawdown:",
"            return (False, f'blocked_drawdown_{current_drawdown:.4f}')",
"",
"        # Near-blocked is the warning zone before hard blocked.",
"        # Drawdown is negative, so near threshold is less negative than blocked.",
"        if current_drawdown <= near_blocked_drawdown:",
"            return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')",
"",
"        if current_drawdown <= caution_drawdown:",
"            try:",
"                current_exposure = float(getattr(portfolio, 'exposure', 0.0) or 0.0)",
"            except Exception:",
"                current_exposure = 0.0",
"            try:",
"                exposure_pct = current_exposure / max(float(equity or 0.0), 1e-09)",
"            except Exception:",
"                exposure_pct = 0.0",
"            if open_positions_count >= 2:",
"                return (False, f'caution_drawdown_position_cap_{current_drawdown:.4f}')",
"            if exposure_pct >= 0.5:",
"                return (False, f'caution_drawdown_exposure_{current_drawdown:.4f}_{exposure_pct:.4f}')",
"    except Exception:",
"        pass",
]

patched = lines[:start] + new_block + lines[end:]
path.write_text("\n".join(patched) + "\n", encoding="utf-8")

print("MAIN_CAN_BUY_ROBUST_PATCH_OK")
print("REPLACED_LINES", start + 1, "TO", end)
