import json
import re
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

recon = read("final_reconciliation_after_restart.txt")
post_recon = read("post_reconciliation.txt")
tria = read("tria_aggregation_check.txt")
trades = read("post_trades.txt")
positions = read("post_positions_positive.txt")
logs = read("runtime_error_scan.txt")
status_text = read("status_after_restart.json")
sql_out = read("aggregation_fix_sql_output.txt")

failures = []
warnings = []

if "COMMIT" not in sql_out:
    failures.append("aggregation SQL did not commit")

if "qfos_trades_aiud_position_rebuild" not in sql_out and "CREATE TRIGGER" not in sql_out:
    warnings.append("trigger creation not confidently observed in SQL output")

# Fail if any reconciliation row shows non-zero missing_qty.
# Parse psql rows loosely: symbol columns with numeric missing qty.
for line in recon.splitlines():
    if "/USDT" not in line or "|" not in line:
        continue
    parts = [p.strip() for p in line.split("|")]
    # Expected layout:
    # symbol | expected_open_qty | actual_open_qty | missing_qty | expected_avg_entry | actual_avg_entry | exposure
    if len(parts) >= 4:
        try:
            missing = float(parts[3])
            if abs(missing) > 0.00001:
                failures.append(f"non-zero missing_qty row: {line}")
        except Exception:
            pass

if "GUA/USDT" in trades and "GUA/USDT" not in positions:
    failures.append("GUA BUY exists but GUA position missing")

if "TRIA/USDT" in trades:
    if "109.948" not in tria and "109.948" not in recon and "109.948" not in positions:
        failures.append("TRIA aggregated quantity not observed near expected 109.948197")

if "Traceback" in logs:
    failures.append("runtime log contains Traceback")
if "OperationalError" in logs:
    failures.append("runtime log contains OperationalError")
if "unable to open database file" in logs:
    failures.append("runtime log contains unable to open database file")
if "Bot loop error" in logs:
    failures.append("runtime log contains Bot loop error")
if "SyntaxError" in logs:
    failures.append("runtime log contains SyntaxError")

try:
    status = json.loads(status_text)
    st_positions = status.get("positions", [] if "positions" in status else {})
    portfolio = status.get("portfolio", {}) or {}
    exposure = float(portfolio.get("exposure", 0.0) or 0.0)

    if exposure < -0.00001:
        failures.append(f"/status exposure is negative: {exposure}")
except Exception as e:
    warnings.append(f"could not parse status JSON: {e}")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
