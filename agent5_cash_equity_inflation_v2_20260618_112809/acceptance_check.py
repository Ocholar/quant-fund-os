import json
import re
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

status_text = read("status_after_rebuild.json")
latest_text = read("portfolio_latest_after_rebuild.json")
ledger = read("final_db_ledger_accounting.txt")
delta = read("required_snapshot_delta_check.txt")
recon = read("final_position_reconciliation.txt")
strategy = read("final_positions_strategy_check.txt")
logs = read("runtime_log_scan.txt")
sql_out = read("cash_equity_sql_output.txt")

failures = []
warnings = []

if "COMMIT" not in sql_out:
    failures.append("cash/equity SQL did not commit")

if "QFOS_CASH_EQUITY_AUTHORITY" not in logs:
    failures.append("runtime cash/equity authority log not observed")

for pat in ["unable to open database file", "OperationalError", "Traceback", "Bot loop error", "SyntaxError"]:
    if pat in logs:
        failures.append(f"runtime log contains {pat}")

# missing_qty must be zero.
for line in recon.splitlines():
    if "/USDT" not in line or "|" not in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 4:
        try:
            missing = float(parts[3])
            if abs(missing) > 0.00001:
                failures.append(f"non-zero missing_qty row: {line}")
        except Exception:
            pass

# No repeated net_qty_guard strategy pollution.
if "net_qty_guard|net_qty_guard" in strategy:
    failures.append("repeated net_qty_guard strategy pollution remains")

# Parse delta check rows.
for line in delta.splitlines():
    if "|" not in line or "latest_snapshot_cash" in line or "---" in line or "row" in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 11:
        try:
            cash_delta = float(parts[3])
            exposure_delta = float(parts[6])
            equity_delta = float(parts[9])
            if abs(cash_delta) > 0.05:
                failures.append(f"snapshot cash_delta not near 0: {cash_delta}")
            if abs(exposure_delta) > 0.05:
                failures.append(f"snapshot exposure_delta not near 0: {exposure_delta}")
            if abs(equity_delta) > 0.05:
                failures.append(f"snapshot equity_delta not near 0: {equity_delta}")
        except Exception:
            pass

try:
    status = json.loads(status_text)
    p = status.get("portfolio", {}) or {}
    perf = status.get("performance", {}) or {}
    trading = status.get("trading", {}) or {}
    positions = status.get("positions", []) or []

    cash = float(p.get("cash", 0.0) or 0.0)
    equity = float(p.get("equity", 0.0) or 0.0)
    exposure = float(p.get("exposure", 0.0) or 0.0)
    realized = float(p.get("realized_pnl", perf.get("realized_pnl", 0.0)) or 0.0)
    unrealized = float(p.get("unrealized_pnl", perf.get("unrealized_pnl", 0.0)) or 0.0)
    total_pnl = float(p.get("total_pnl", perf.get("total_pnl", 0.0)) or 0.0)
    sell_count = int(trading.get("sell_count", perf.get("sell_count", 0)) or 0)
    buy_count = int(trading.get("buy_count", perf.get("buy_count", 0)) or 0)

    listed_exposure = 0.0
    if isinstance(positions, list):
        for row in positions:
            if isinstance(row, dict):
                listed_exposure += float(row.get("exposure", 0.0) or 0.0)

    if buy_count > 0 and cash > 100.05:
        failures.append(f"/status cash exceeds starting cash after BUYs: cash={cash}")

    if abs(equity - (cash + exposure)) > 0.10:
        failures.append(f"/status equity != cash + exposure: equity={equity} cash={cash} exposure={exposure}")

    if listed_exposure > 0 and abs(exposure - listed_exposure) > 0.25:
        failures.append(f"/status exposure mismatch listed positions: status={exposure} listed={listed_exposure}")

    if sell_count == 0 and abs(realized) > 0.05:
        failures.append(f"/status realized_pnl non-zero with 0 sells: realized={realized}")

    if abs(total_pnl - (realized + unrealized)) > 0.05:
        failures.append(f"/status total_pnl != realized + unrealized: total={total_pnl} realized={realized} unrealized={unrealized}")

except Exception as e:
    failures.append(f"failed to parse /status JSON: {e}")

if latest_text.strip():
    try:
        latest = json.loads(latest_text)
        lp = latest.get("portfolio", latest) if isinstance(latest, dict) else {}
        lc = float(lp.get("cash", cash) or 0.0)
        le = float(lp.get("equity", equity) or 0.0)
        lx = float(lp.get("exposure", exposure) or 0.0)

        if lc > 100.05:
            failures.append(f"/portfolio/latest cash exceeds starting cash after BUYs: cash={lc}")
        if abs(le - (lc + lx)) > 0.10:
            failures.append(f"/portfolio/latest equity != cash + exposure: equity={le} cash={lc} exposure={lx}")
    except Exception as e:
        warnings.append(f"could not parse /portfolio/latest JSON: {e}")
else:
    warnings.append("/portfolio/latest unavailable or failed; inspect portfolio_latest_after_rebuild_error.txt")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
