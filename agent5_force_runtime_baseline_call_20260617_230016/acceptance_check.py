import json
from pathlib import Path
import os

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

status_text = read("status_after_forced_sync.json")
pos_text = read("positive_positions_after_runtime.txt")
trades_text = read("trades_count_after_runtime.txt")
archive_text = read("orphan_archive_before_rebuild.txt")
log_text = read("runtime_log_check.txt")

failures = []
warnings = []

if "ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE" not in archive_text:
    failures.append("orphan archive missing expected rows")

if "(0 rows)" not in pos_text:
    failures.append("positive positions remain after runtime")

if " 0" not in trades_text:
    warnings.append("could not confidently parse trades count as 0")

if "QFOS_RUNTIME_BASELINE_SYNC" not in log_text:
    failures.append("runtime baseline sync log not observed")

try:
    status = json.loads(status_text)
    portfolio = status.get("portfolio", {}) or {}
    positions = status.get("positions", []) or status.get("positions", {}) or []
    trading = status.get("trading", {}) or {}
    performance = status.get("performance", {}) or {}

    equity = float(portfolio.get("equity", -1))
    cash = float(portfolio.get("cash", -1))
    exposure = float(portfolio.get("exposure", -1))
    total_trades = int(trading.get("total_trades", performance.get("total_trades", -1)))
    buy_count = int(trading.get("buy_count", performance.get("buy_count", -1)))
    sell_count = int(trading.get("sell_count", performance.get("sell_count", -1)))

    if abs(equity - 100.0) > 0.0001:
        failures.append(f"status equity not 100: {equity}")
    if abs(cash - 100.0) > 0.0001:
        failures.append(f"status cash not 100: {cash}")
    if abs(exposure) > 0.0001:
        failures.append(f"status exposure not zero: {exposure}")
    if positions not in ([], {}, None):
        failures.append(f"status positions not empty: {positions}")
    if total_trades != 0:
        failures.append(f"status total_trades not zero: {total_trades}")
    if buy_count != 0 or sell_count != 0:
        failures.append(f"status buy/sell counts not zero: buy={buy_count} sell={sell_count}")

except Exception as e:
    failures.append(f"failed to parse status JSON: {e}")

for pat in ["unable to open database file", "OperationalError", "Traceback", "Bot loop error", "SyntaxError"]:
    if pat in log_text:
        failures.append(f"runtime log contains {pat}")

result = {
    "verdict": "PASS" if not failures else "FAIL",
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
