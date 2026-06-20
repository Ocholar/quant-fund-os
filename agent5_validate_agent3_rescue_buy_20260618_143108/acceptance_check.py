import json
import re
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

latest_trades = read("latest_trades.txt")
dup = read("gua_duplicate_buy_check.txt")
rescue_count = read("gua_allocator_rescue_buy_count.txt")
gua_pos = read("gua_position.txt")
recon = read("full_reconciliation.txt")
orphans = read("orphan_positions_check.txt")
acct = read("cash_equity_accounting.txt")
status_text = read("status.json")
portfolio_text = read("portfolio_latest.json")
api_summary = read("api_summary.txt")
logs = read("runtime_log_scan.txt")

failures = []
warnings = []

# 1. GUA rescue BUY must exist.
if "GUA/USDT" not in latest_trades and "GUA/USDT" not in rescue_count:
    failures.append("GUA/USDT BUY not found in trades")

if "evo_allocator_rescue" not in latest_trades and "evo_allocator_rescue" not in rescue_count:
    failures.append("evo_allocator_rescue BUY not found")

# 2. Exactly one GUA allocator rescue BUY expected for this validation window.
# Parse the aggregate row loosely.
for line in rescue_count.splitlines():
    if "|" not in line or "gua_allocator_rescue_buy_count" in line or "---" in line or "row" in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 1:
        try:
            n = int(float(parts[0]))
            if n != 1:
                failures.append(f"GUA evo_allocator_rescue BUY count expected 1, got {n}")
        except Exception:
            pass

# 3. Duplicate groups should not show duplicate_count > 1 for exact same fill.
for line in dup.splitlines():
    if "GUA/USDT" not in line or "|" not in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 6:
        try:
            duplicate_count = int(float(parts[5]))
            if duplicate_count > 1:
                failures.append(f"duplicate exact GUA BUY fill observed: {line}")
        except Exception:
            pass

# 4. GUA position exists.
if "GUA/USDT" not in gua_pos:
    failures.append("GUA/USDT position missing")

# 5. missing_qty = 0 for every symbol.
for line in recon.splitlines():
    if "/USDT" not in line or "|" not in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    # symbol | buy_qty | sell_qty | expected_open_qty | actual_open_qty | missing_qty | ...
    if len(parts) >= 6:
        try:
            missing = float(parts[5])
            if abs(missing) > 0.00001:
                failures.append(f"non-zero missing_qty row: {line}")
        except Exception:
            pass

# 6. Orphan positions must be none.
if "/USDT" in orphans and "(0 rows)" not in orphans:
    failures.append(f"orphan positions found: {orphans}")

# 7. Accounting deltas near zero.
for line in acct.splitlines():
    if "|" not in line or "actual_cash" in line or "---" in line or "row" in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    # created_at | buy_rows | sell_rows | buy_cost | sell_proceeds | open_positions |
    # actual_cash | expected_cash | cash_delta | actual_exposure | expected_exposure |
    # exposure_delta | actual_equity | expected_equity | equity_delta | drawdown
    if len(parts) >= 15:
        try:
            cash_delta = float(parts[8])
            exposure_delta = float(parts[11])
            equity_delta = float(parts[14])
            if abs(cash_delta) > 0.05:
                failures.append(f"cash_delta not near 0: {cash_delta}")
            if abs(exposure_delta) > 0.05:
                failures.append(f"exposure_delta not near 0: {exposure_delta}")
            if abs(equity_delta) > 0.05:
                failures.append(f"equity_delta not near 0: {equity_delta}")
        except Exception:
            pass

# 8. /status equity = cash + exposure and positions exposure matches.
try:
    status = json.loads(status_text)
    p = status.get("portfolio", {}) or {}
    positions = status.get("positions", []) or []
    cash = float(p.get("cash", 0.0) or 0.0)
    exposure = float(p.get("exposure", 0.0) or 0.0)
    equity = float(p.get("equity", 0.0) or 0.0)

    if abs(equity - (cash + exposure)) > 0.10:
        failures.append(f"/status equity != cash + exposure: equity={equity} cash={cash} exposure={exposure}")

    listed_exposure = 0.0
    if isinstance(positions, list):
        for row in positions:
            if isinstance(row, dict):
                listed_exposure += float(row.get("exposure", 0.0) or 0.0)

    if listed_exposure > 0 and abs(exposure - listed_exposure) > 0.25:
        failures.append(f"/status exposure mismatch listed positions: status={exposure} listed={listed_exposure}")

except Exception as e:
    failures.append(f"failed to parse status JSON: {e}")

# 9. /portfolio/latest if available.
if portfolio_text.strip():
    try:
        latest = json.loads(portfolio_text)
        lp = latest.get("portfolio", latest) if isinstance(latest, dict) else {}
        lc = float(lp.get("cash", cash) or 0.0)
        le = float(lp.get("equity", equity) or 0.0)
        lx = float(lp.get("exposure", exposure) or 0.0)
        if abs(le - (lc + lx)) > 0.10:
            failures.append(f"/portfolio/latest equity != cash + exposure: equity={le} cash={lc} exposure={lx}")
    except Exception as e:
        warnings.append(f"could not parse /portfolio/latest JSON: {e}")
else:
    warnings.append("/portfolio/latest unavailable; inspect portfolio_latest_error.txt")

# 10. Runtime fatal errors.
for pat in ["unable to open database file", "OperationalError", "Traceback", "Bot loop error", "SyntaxError"]:
    if pat in logs:
        failures.append(f"runtime log contains {pat}")

# 11. Check stale fill mutation: later final_applied_fills=0 should not create duplicate.
if "final_applied_fills=0" not in logs:
    warnings.append("final_applied_fills=0 not found in scanned logs; may be outside tail window")

if "SELL_VALIDATION_REJECT" in logs:
    warnings.append("SELL_VALIDATION_REJECT observed; inspect whether it is a storm or normal guard")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
