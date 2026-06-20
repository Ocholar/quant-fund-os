import json
import re
from pathlib import Path

report_dir = Path(__import__("os").environ["REPORT_DIR"])

positions_text = (report_dir / "positions_rows.txt").read_text(encoding="utf-8", errors="ignore")
trades_text = (report_dir / "trades_rows.txt").read_text(encoding="utf-8", errors="ignore")
lineage_text = (report_dir / "ledger_lineage_audit_output.txt").read_text(encoding="utf-8", errors="ignore")
code_text = (report_dir / "code_position_write_scan.txt").read_text(encoding="utf-8", errors="ignore")

has_orphan = "ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE" in lineage_text
has_valid = "VALID_TRACEABLE_POSITION" in lineage_text
has_bad_exit = "PROTECTIVE_SELLS_BAD_EXIT_ACCOUNTING" in lineage_text and re.search(r"\n\s*\d+\s+\|", lineage_text) is not None
has_negative = "NEGATIVE_POSITIONS" in lineage_text and re.search(r"\n\s*[A-Z0-9()/]+/USDT\s+\|", lineage_text) is not None

seed_markers = [
    "seed",
    "seeded",
    "baseline",
    "test",
    "reconciled",
    "paper_position_sync",
    "reset",
]
lineage_lower = lineage_text.lower()
has_marker = any(m in lineage_lower for m in seed_markers)

# Specific PM concern: open positions with trades=0.
trades_empty = (
    "0 rows" in trades_text.lower()
    or "(0 rows)" in trades_text.lower()
    or re.search(r"\n\s*id\s+\|", trades_text.lower()) is None
)

classification = {
    "has_orphan_open_positions": has_orphan,
    "has_valid_traceable_positions": has_valid,
    "has_seed_or_reconcile_markers": has_marker,
    "trades_appear_empty": trades_empty,
    "bad_exit_accounting_detected": has_bad_exit,
    "negative_position_detected": has_negative,
    "verdict": None,
    "finding": None,
}

if has_orphan and trades_empty:
    classification["verdict"] = "FAIL"
    classification["finding"] = (
        "Open positions exist without trade/fill lineage. With trades empty, these are invalid orphan positions unless PM explicitly confirms they are seeded test state."
    )
elif has_orphan:
    classification["verdict"] = "FAIL"
    classification["finding"] = "At least one open position has no trade lineage."
elif has_valid and not has_bad_exit and not has_negative:
    classification["verdict"] = "PASS"
    classification["finding"] = "Open positions are traceable to trade lineage and no critical accounting defects were detected."
else:
    classification["verdict"] = "FAIL"
    classification["finding"] = "Ledger could not be proven consistent from available evidence."

(report_dir / "classification.json").write_text(json.dumps(classification, indent=2), encoding="utf-8")

print(json.dumps(classification, indent=2))
