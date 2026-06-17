from pathlib import Path
import json, re

out = Path(".")
logs = Path("logs_merged.txt").read_text(encoding="utf-8", errors="replace")

def count(pattern):
    return len(re.findall(pattern, logs, flags=re.I))

def lines(pattern, limit=20):
    found = []
    for line in logs.splitlines():
        if re.search(pattern, line, flags=re.I):
            found.append(line)
    return found[-limit:]

status_files = sorted(Path(".").glob("status_sample_*.json"))
statuses = []
for f in status_files:
    try:
        statuses.append(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        pass

final_status = {}
try:
    final_status = json.loads(Path("status_final.json").read_text(encoding="utf-8"))
except Exception:
    pass

db_final = {}
try:
    db_final = json.loads(Path("db_final.json").read_text(encoding="utf-8"))
except Exception:
    pass

feature_empty_count = count(r"FEATURES:\s*\{\}|Feature symbols:\s*0|Normal FEATURES is empty|FALLBACK FEATURES:\s*\{\}")
normal_feature_positive = count(r"Feature symbols:\s*[1-9]|ready_features['\"]?:\s*[1-9]|normal_features['\"]?:\s*[1-9]")
trusted_count_seen = count(r"trusted_count")
validated_prices_seen = count(r"MARKET TICK DATA VALIDATED:\s*\{[^}]")
allocator_blocks = count(r"ALLOCATOR BLOCK")
allocator_rescue = count(r"ALLOCATOR_RESCUE")
orders_nonzero = count(r"ORDERS:\s*\[[^\]]+\]|raw_orders=[1-9]|proposed_fills=[1-9]")
final_applied_nonzero = count(r"final_applied_fills=[1-9]")
entry_top_empty = count(r"ENTRY QUALITY TOP 10:\s*\[\]|QUALITY_RANK\] top-0")
entry_top_nonempty = count(r"ENTRY QUALITY TOP 10:\s*\[\(")
sell_rejects = count(r"SELL_VALIDATION_REJECT")
dup_guard = count(r"duplicate|DUPLICATE")
tracebacks = count(r"Traceback|SyntaxError|Bot loop error")
status_200 = count(r"GET /status HTTP/1.1\" 200 OK")

negative_positions = db_final.get("negative_positions", [])
recent_green_to_red = db_final.get("recent_green_to_red", [])
recent_trades = db_final.get("recent_trades", [])

risk_status = final_status.get("risk_status")
bot_state = final_status.get("bot_state")
live_trading = final_status.get("live_trading")
portfolio = final_status.get("portfolio", {})
positions = final_status.get("positions", [])

classification = "UNKNOWN"
owner = "UNKNOWN"
reason = ""

if tracebacks:
    classification = "RUNTIME ERROR"
    owner = "Agent 1"
    reason = "SyntaxError/Traceback/Bot loop error detected."
elif negative_positions or recent_green_to_red:
    classification = "EXECUTION SAFETY ISSUE"
    owner = "Agent 5"
    reason = "Negative positions or new green-to-red duplicate SELLs detected."
elif bot_state != "RUNNING" or live_trading is not False:
    classification = "API/RUNTIME STATUS ISSUE"
    owner = "Agent 6" if bot_state else "Agent 1"
    reason = "Status unhealthy, bot not RUNNING, or live_trading not false."
elif feature_empty_count and not normal_feature_positive:
    classification = "BLOCKED"
    owner = "Agent 4"
    reason = "Market prices validate, but NORMAL feature generation remains empty after warmup."
elif normal_feature_positive and not orders_nonzero and allocator_blocks:
    classification = "BLOCKED"
    owner = "Agent 3"
    reason = "Features exist but strategy/allocator emits no usable orders."
elif normal_feature_positive and orders_nonzero and not final_applied_nonzero:
    classification = "BLOCKED"
    owner = "Agent 2 or Agent 3"
    reason = "Orders appear, but risk/quality gates prevent final applied fills."
else:
    classification = "CORRECTLY WAITING"
    owner = "None"
    reason = "Runtime healthy, no execution safety issue, and no clear hard block pattern detected."

report = []
report.append("# Phase 2 Supervised Runtime Flow Audit")
report.append("")
report.append("## Verdict")
report.append(f"- Classification: {classification}")
report.append(f"- Owning component: {owner}")
report.append(f"- Reason: {reason}")
report.append("")
report.append("## Runtime Health")
report.append(f"- bot_state: {bot_state}")
report.append(f"- live_trading: {live_trading}")
report.append(f"- risk_status: {risk_status}")
report.append(f"- status 200 log hits: {status_200}")
report.append(f"- SyntaxError / Traceback / Bot loop error hits: {tracebacks}")
report.append("")
report.append("## Market Data / Features")
report.append(f"- trusted_count log hits: {trusted_count_seen}")
report.append(f"- validated price map hits: {validated_prices_seen}")
report.append(f"- feature-empty diagnostics: {feature_empty_count}")
report.append(f"- normal/ready feature positive hits: {normal_feature_positive}")
report.append("")
report.append("## Strategy / Allocator")
report.append(f"- allocator block hits: {allocator_blocks}")
report.append(f"- allocator rescue hits: {allocator_rescue}")
report.append(f"- nonzero orders/proposed_fills hits: {orders_nonzero}")
report.append(f"- nonzero final_applied_fills hits: {final_applied_nonzero}")
report.append("")
report.append("## Entry Quality")
report.append(f"- empty quality rank hits: {entry_top_empty}")
report.append(f"- nonempty ENTRY QUALITY TOP 10 hits: {entry_top_nonempty}")
report.append("")
report.append("## Execution Safety")
report.append(f"- SELL_VALIDATION_REJECT hits: {sell_rejects}")
report.append(f"- duplicate-guard related hits: {dup_guard}")
report.append(f"- negative positions: {negative_positions if negative_positions else 'NONE'}")
report.append(f"- recent sideways_green_to_red_exit in 30m: {recent_green_to_red if recent_green_to_red else 'NONE'}")
report.append("")
report.append("## Recent Trades")
if recent_trades:
    for row in recent_trades[:20]:
        report.append(f"- {row}")
else:
    report.append("- NONE")
report.append("")
report.append("## Evidence: Market / Feature Lines")
for line in lines(r"trusted_count|MARKET TICK DATA VALIDATED|Normal FEATURES is empty|FALLBACK FEATURES|Feature symbols|FEATURES:", 25):
    report.append(f"- {line}")
report.append("")
report.append("## Evidence: Allocator / Entry Quality Lines")
for line in lines(r"STRATEGY SCORE DEBUG|ALLOCATOR BLOCK|ALLOCATOR_RESCUE|ENTRY QUALITY TOP 10|QUALITY_RANK|raw_orders|proposed_fills|final_applied_fills|ORDERS:", 35):
    report.append(f"- {line}")
report.append("")
report.append("## Evidence: Safety Lines")
for line in lines(r"SELL_VALIDATION_REJECT|sideways_green_to_red_exit|NEGATIVE|Traceback|SyntaxError|Bot loop error|GET /status", 35):
    report.append(f"- {line}")

Path("phase2_runtime_audit_report.md").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report))
