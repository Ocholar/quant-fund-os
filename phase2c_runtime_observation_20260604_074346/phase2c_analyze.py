from pathlib import Path
import json, re

root = Path(".")
logs = (root / "logs_merged.txt").read_text(encoding="utf-8", errors="replace")

def load_json(name):
    p = root / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_parse_error": str(e), "_raw_start": p.read_text(encoding="utf-8", errors="replace")[:500]}

def count(pattern):
    return len(re.findall(pattern, logs, flags=re.I))

def matching_lines(pattern, limit=40):
    out = []
    for line in logs.splitlines():
        if re.search(pattern, line, flags=re.I):
            out.append(line)
    return out[-limit:]

baseline_status = load_json("status_baseline.json")
final_status = load_json("status_final.json")
db_baseline = load_json("db_baseline.json")
db_final = load_json("db_final.json")

baseline_trade_count = db_baseline.get("trade_count")
final_trade_count = db_final.get("trade_count")
baseline_max_trade_id = db_baseline.get("max_trade_id")
final_max_trade_id = db_final.get("max_trade_id")

new_trade_count = None
if isinstance(baseline_trade_count, int) and isinstance(final_trade_count, int):
    new_trade_count = final_trade_count - baseline_trade_count

new_trade_id_delta = None
if isinstance(baseline_max_trade_id, int) and isinstance(final_max_trade_id, int):
    new_trade_id_delta = final_max_trade_id - baseline_max_trade_id

bot_state = final_status.get("bot_state")
live_trading = final_status.get("live_trading")
risk_status = final_status.get("risk_status")
portfolio = final_status.get("portfolio", {})
positions = final_status.get("positions", [])

health = {
    "status_200_hits": count(r'GET /status HTTP/1\.1" 200 OK'),
    "syntax_error_hits": count(r"SyntaxError"),
    "traceback_hits": count(r"Traceback"),
    "bot_loop_error_hits": count(r"Bot loop error"),
    "sqlalchemy_not_exec_hits": count(r"Not an executable object"),
    "sell_validation_reject_hits": count(r"SELL_VALIDATION_REJECT"),
    "market_validated_hits": count(r"MARKET TICK DATA VALIDATED"),
    "feature_symbols_positive_hits": count(r"Feature symbols:\s*[1-9]"),
    "normal_features_hits": count(r"'source': 'NORMAL'|\"source\": \"NORMAL\""),
    "raw_orders_nonzero_hits": count(r"raw_orders=[1-9]|ORDERS:\s*\[[^\]]+\]"),
    "proposed_fills_nonzero_hits": count(r"proposed_fills=[1-9]"),
    "final_applied_fills_nonzero_hits": count(r"final_applied_fills=[1-9]"),
    "allocator_block_hits": count(r"ALLOCATOR BLOCK"),
    "allocator_rescue_hits": count(r"ALLOCATOR_RESCUE"),
    "entry_top_empty_hits": count(r"ENTRY QUALITY TOP 10:\s*\[\]|QUALITY_RANK\] top-0"),
    "entry_top_nonempty_hits": count(r"ENTRY QUALITY TOP 10:\s*\[\("),
}

negative_positions = db_final.get("negative_positions", [])
duplicate_patterns = db_final.get("recent_duplicate_sell_patterns", [])
green_to_red = db_final.get("recent_green_to_red", [])
stuck_after_full_sell = db_final.get("stuck_after_full_sell_candidates", [])

reject_lines = matching_lines(r"SELL_VALIDATION_REJECT", 200)
reject_storm = False
reject_reason_summary = {}
for line in reject_lines:
    m_sym = re.search(r"symbol=([^\s]+)", line)
    m_reason = re.search(r"reason=([^\s]+)", line)
    key = (m_sym.group(1) if m_sym else "UNKNOWN", m_reason.group(1) if m_reason else "UNKNOWN")
    reject_reason_summary[key] = reject_reason_summary.get(key, 0) + 1
if health["sell_validation_reject_hits"] >= 50:
    reject_storm = True

runtime_errors = (
    health["syntax_error_hits"]
    + health["traceback_hits"]
    + health["bot_loop_error_hits"]
    + health["sqlalchemy_not_exec_hits"]
)

stable = True
reasons = []

if bot_state != "RUNNING":
    stable = False
    reasons.append(f"bot_state is not RUNNING: {bot_state}")
if live_trading is not False:
    stable = False
    reasons.append(f"live_trading is not false: {live_trading}")
if runtime_errors:
    stable = False
    reasons.append("runtime error or SQLAlchemy executable error detected")
if negative_positions:
    stable = False
    reasons.append("negative positions detected")
if duplicate_patterns:
    stable = False
    reasons.append("new duplicate SELL patterns detected")
if stuck_after_full_sell:
    stable = False
    reasons.append("stuck open position after full SELL candidate detected")
if reject_storm:
    stable = False
    reasons.append("SELL_VALIDATION_REJECT storm detected")

if not reasons:
    reasons.append("No critical runtime/execution safety issue detected.")

if stable and health["final_applied_fills_nonzero_hits"] > 0:
    loop_verdict = "HEALTHY_AND_TRADING"
elif stable and health["raw_orders_nonzero_hits"] == 0 and health["allocator_block_hits"] > 0:
    loop_verdict = "STABLE_BUT_ALLOCATOR_WAITING_OR_BLOCKED"
elif stable and health["raw_orders_nonzero_hits"] > 0 and health["final_applied_fills_nonzero_hits"] == 0:
    loop_verdict = "STABLE_BUT_RISK_OR_QUALITY_GATE_BLOCKED"
elif stable:
    loop_verdict = "STABLE_WAITING"
else:
    loop_verdict = "NOT_STABLE_FOR_RESET"

owner = "None"
if not stable:
    if negative_positions or duplicate_patterns or stuck_after_full_sell or reject_storm:
        owner = "Agent 5 / execution-persistence"
    elif runtime_errors:
        owner = "Agent 1 runtime / compatibility"
    elif bot_state != "RUNNING":
        owner = "Agent 6 API/runtime surface"
elif loop_verdict in ("STABLE_BUT_ALLOCATOR_WAITING_OR_BLOCKED", "STABLE_BUT_RISK_OR_QUALITY_GATE_BLOCKED"):
    owner = "Agent 3 allocation/strategy, with Agent 2 if hard exposure/risk gate is the blocker"

report = []
report.append("# Phase 2C Runtime Observation Report")
report.append("")
report.append("## Verdict")
report.append(f"- Loop verdict: {loop_verdict}")
report.append(f"- Backend stable enough for clean reset planning: {'YES' if stable else 'NO'}")
report.append(f"- Owning component if blocked/unstable: {owner}")
report.append("- Reasons:")
for r in reasons:
    report.append(f"  - {r}")

report.append("")
report.append("## /status Health")
report.append(f"- bot_state: {bot_state}")
report.append(f"- live_trading: {live_trading}")
report.append(f"- risk_status: {risk_status}")
report.append(f"- status 200 log hits: {health['status_200_hits']}")
report.append(f"- final portfolio: {portfolio}")
report.append(f"- final positions from API: {positions}")

report.append("")
report.append("## Runtime Error Checks")
report.append(f"- SyntaxError hits: {health['syntax_error_hits']}")
report.append(f"- Traceback hits: {health['traceback_hits']}")
report.append(f"- Bot loop error hits: {health['bot_loop_error_hits']}")
report.append(f"- SQLAlchemy 'Not an executable object' hits: {health['sqlalchemy_not_exec_hits']}")

report.append("")
report.append("## Trade / Execution Safety")
report.append(f"- baseline trade_count: {baseline_trade_count}")
report.append(f"- final trade_count: {final_trade_count}")
report.append(f"- new trades during window: {new_trade_count}")
report.append(f"- baseline max_trade_id: {baseline_max_trade_id}")
report.append(f"- final max_trade_id: {final_max_trade_id}")
report.append(f"- max_trade_id delta: {new_trade_id_delta}")
report.append(f"- negative positions: {negative_positions if negative_positions else 'NONE'}")
report.append(f"- recent duplicate SELL patterns: {duplicate_patterns if duplicate_patterns else 'NONE'}")
report.append(f"- recent sideways_green_to_red_exit rows: {green_to_red if green_to_red else 'NONE'}")
report.append(f"- stuck-after-full-SELL candidates: {stuck_after_full_sell if stuck_after_full_sell else 'NONE'}")
report.append(f"- SELL_VALIDATION_REJECT hits: {health['sell_validation_reject_hits']}")
report.append(f"- SELL_VALIDATION_REJECT storm: {'YES' if reject_storm else 'NO'}")
report.append(f"- SELL_VALIDATION_REJECT reason summary: {reject_reason_summary if reject_reason_summary else 'NONE'}")

report.append("")
report.append("## Market Data / Features")
report.append(f"- MARKET TICK DATA VALIDATED hits: {health['market_validated_hits']}")
report.append(f"- Feature symbols positive hits: {health['feature_symbols_positive_hits']}")
report.append(f"- NORMAL feature hits: {health['normal_features_hits']}")

report.append("")
report.append("## Allocator / Strategy Flow")
report.append(f"- raw_orders nonzero hits: {health['raw_orders_nonzero_hits']}")
report.append(f"- proposed_fills nonzero hits: {health['proposed_fills_nonzero_hits']}")
report.append(f"- final_applied_fills nonzero hits: {health['final_applied_fills_nonzero_hits']}")
report.append(f"- allocator block hits: {health['allocator_block_hits']}")
report.append(f"- allocator rescue hits: {health['allocator_rescue_hits']}")
report.append(f"- empty ENTRY QUALITY TOP 10 hits: {health['entry_top_empty_hits']}")
report.append(f"- non-empty ENTRY QUALITY TOP 10 hits: {health['entry_top_nonempty_hits']}")

report.append("")
report.append("## Evidence: Safety Lines")
for line in matching_lines(r"SELL_VALIDATION_REJECT|duplicate|NEGATIVE|Traceback|SyntaxError|Bot loop error|Not an executable object|GET /status", 80):
    report.append(f"- {line}")

report.append("")
report.append("## Evidence: Allocator / Entry Lines")
for line in matching_lines(r"STRATEGY SCORE DEBUG|ALLOCATOR BLOCK|ALLOCATOR_RESCUE|ENTRY QUALITY TOP 10|QUALITY_RANK|raw_orders|proposed_fills|final_applied_fills|ORDERS:", 80):
    report.append(f"- {line}")

report.append("")
report.append("## Evidence: Market / Feature Lines")
for line in matching_lines(r"MARKET TICK DATA VALIDATED|Feature symbols|FEATURES:", 20):
    report.append(f"- {line}")

Path("phase2c_runtime_observation_report.md").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report))
