import json
import os
from pathlib import Path

report_dir = Path(os.environ["REPORT_DIR"])

def read(name):
    p = report_dir / name
    return p.read_text(encoding="utf-8-sig", errors="ignore") if p.exists() else ""

logs = read("controlled_window_runtime_logs.txt")
fresh = read("fresh_trades_controlled_window.txt")
dups = read("new_duplicate_sell_check.txt")
oversell = read("new_oversell_check.txt")
state = read("final_runtime_state.txt")
acct = read("accounting_reconciliation_after_window.txt")

failures = []
warnings = []

fatal_tokens = [
    "EXIT_SELL_ERROR",
    "NameError",
    "FILL_PERSISTENCE_ERROR",
    "Traceback",
    "Bot loop error",
    "SyntaxError",
]

for token in fatal_tokens:
    if token in logs:
        failures.append(f"runtime log contains {token}")

if "paused=True" not in state:
    failures.append("bot did not end paused")

if "/USDT" in dups and "(0 rows)" not in dups:
    failures.append("new duplicate SELL detected during controlled window")

if "/USDT" in oversell and "(0 rows)" not in oversell:
    failures.append("new oversell detected during controlled window")

fresh_sell = (
    "| sell " in fresh.lower()
    or "| sell |" in fresh.lower()
)

persisted_sell = (
    "FILL_PERSISTED_ATOMIC" in logs
    and "side=sell" in logs.lower()
)

audit_persisted = (
    "EXIT_SELL_AUDIT" in logs
    and "decision=PERSISTED" in logs
)

exit_meta = (
    "is_exit" in fresh
    and "exit_reason" in fresh
)

if persisted_sell and audit_persisted:
    outcome = "PASS"
elif fresh_sell or "EXIT_SELL_AUDIT" in logs:
    outcome = "FAIL"
else:
    outcome = "CONDITIONAL PASS"
    warnings.append("No eligible exit observed during the ten-minute controlled window; fresh exit persistence remains unproven.")

if outcome == "PASS" and not exit_meta:
    failures.append("SELL occurred but trade output did not expose is_exit and exit_reason")
    outcome = "FAIL"

if failures:
    outcome = "FAIL"

result = {
    "verdict": outcome,
    "fresh_sell_observed": fresh_sell,
    "exit_audit_persisted_observed": audit_persisted,
    "atomic_sell_persist_observed": persisted_sell,
    "failures": failures,
    "warnings": warnings,
}

(report_dir / "acceptance_result.json").write_text(
    json.dumps(result, indent=2),
    encoding="utf-8"
)
print(json.dumps(result, indent=2))
