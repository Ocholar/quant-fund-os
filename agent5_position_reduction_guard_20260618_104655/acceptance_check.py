import json
import re
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

recon = read("final_reconciliation_after_restart.txt")
gua_tria = read("gua_tria_check.txt")
logs = read("runtime_log_scan.txt")
sql_out = read("guard_sql_output.txt")

failures = []
warnings = []

if "COMMIT" not in sql_out:
    failures.append("guard SQL did not commit")

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

if "GUA/USDT" not in gua_tria or "1.7782916" not in gua_tria:
    failures.append("GUA position not restored to expected quantity")

if "TRIA/USDT" not in gua_tria or "109.948" not in gua_tria:
    failures.append("TRIA aggregated position not observed")

for pat in ["unable to open database file", "OperationalError", "Traceback", "Bot loop error", "SyntaxError"]:
    if pat in logs:
        failures.append(f"runtime log contains {pat}")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
