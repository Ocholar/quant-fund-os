import json
import re
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

logs = read("runtime_log_after_observation.txt")
audit = read("exit_decision_audit_after_observation.txt")
sells = read("sell_rows_after_observation.txt")
recon = read("position_reconciliation_after_observation.txt")
acct = read("accounting_delta_after_observation.txt")
open_pos = read("open_positions_after_observation.txt")
status_text = read("status_after_observation.json")

failures = []
warnings = []

if "[EXIT_DECISION]" not in logs and "EXIT_DECISION" not in audit:
    failures.append("no EXIT_DECISION logs/audit observed")

# At least one old/stagnant or threshold-qualified position should sell.
# If no sell happened, check whether there were qualifying old positions.
has_sell = "/USDT" in sells and "| sell" in sells.lower()
if not has_sell:
    # If any audit row has decision SELL but no sell row, fail.
    if " SELL " in audit or "| SELL |" in audit:
        failures.append("exit audit shows SELL decision but no SELL trade row observed")
    else:
        warnings.append("no SELL row observed; verify no position qualified for exit during observation")

# SELL rows must have is_exit and reason.
for line in sells.splitlines():
    if "/USDT" not in line or "|" not in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    # id | symbol | side | quantity | fill_price | pnl | strategy | is_exit | exit_reason | created_at
    if len(parts) >= 9:
        side = parts[2].lower()
        is_exit = parts[7].lower()
        exit_reason = parts[8]
        if side == "sell":
            if is_exit not in ("t", "true", "1"):
                failures.append(f"SELL row is_exit not true: {line}")
            if exit_reason in ("", "None", "null"):
                failures.append(f"SELL row missing exit_reason: {line}")

# Position reconciliation missing_qty must be zero.
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

# Accounting deltas must remain zero-ish.
for line in acct.splitlines():
    if "|" not in line or "latest_snapshot_cash" in line or "---" in line or "row" in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) >= 11:
        try:
            cash_delta = float(parts[3])
            exposure_delta = float(parts[6])
            equity_delta = float(parts[9])
            if abs(cash_delta) > 0.05:
                failures.append(f"cash_delta not near zero: {cash_delta}")
            if abs(exposure_delta) > 0.05:
                failures.append(f"exposure_delta not near zero: {exposure_delta}")
            if abs(equity_delta) > 0.05:
                failures.append(f"equity_delta not near zero: {equity_delta}")
        except Exception:
            pass

# Fatal logs.
for pat in ["unable to open database file", "OperationalError", "Traceback", "Bot loop error", "SyntaxError"]:
    if pat in logs:
        failures.append(f"runtime log contains {pat}")

# Check /status basic parse and realized/sell relationship.
try:
    status = json.loads(status_text)
    perf = status.get("performance", {}) or {}
    trading = status.get("trading", {}) or {}
    sell_count = int(trading.get("sell_count", perf.get("sell_count", 0)) or 0)
    realized = float((status.get("portfolio", {}) or {}).get("realized_pnl", perf.get("realized_pnl", 0.0)) or 0.0)
    if sell_count == 0 and abs(realized) > 0.05:
        failures.append(f"realized_pnl nonzero with zero sells: {realized}")
except Exception as e:
    warnings.append(f"could not parse status JSON: {e}")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
