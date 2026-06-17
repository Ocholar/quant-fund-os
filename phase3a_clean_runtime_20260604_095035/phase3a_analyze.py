from pathlib import Path
import json, re, statistics

base = Path(".")
logs = Path("logs_merged.txt").read_text(encoding="utf-8", errors="replace") if Path("logs_merged.txt").exists() else ""

def count(pattern):
    return len(re.findall(pattern, logs, flags=re.I))

def sample_lines(pattern, limit=40):
    out = []
    for line in logs.splitlines():
        if re.search(pattern, line, flags=re.I):
            out.append(line)
    return out[-limit:]

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}

baseline_status = read_json("status_baseline.json")
final_status = read_json("status_final.json")
baseline_db = read_json("db_baseline.json")
final_db = read_json("db_final.json")

status_files = sorted(base.glob("status_sample_*.json"))
statuses = []
for f in status_files:
    try:
        statuses.append(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        pass

equities = []
cash_values = []
exposures = []
position_counts = []
trade_counts = []
buy_counts = []
sell_counts = []
bot_states = []
live_flags = []
risk_statuses = []

for s in statuses:
    p = s.get("portfolio", {}) or {}
    perf = s.get("performance", {}) or {}
    trading = s.get("trading", {}) or {}

    if "equity" in p:
        equities.append(float(p.get("equity") or 0))
    if "cash" in p:
        cash_values.append(float(p.get("cash") or 0))
    if "exposure" in p:
        exposures.append(float(p.get("exposure") or 0))

    position_counts.append(len(s.get("positions", []) or []))
    trade_counts.append(int((trading.get("total_trades", perf.get("total_trades", 0)) or 0)))
    buy_counts.append(int((trading.get("buy_count", perf.get("buy_count", 0)) or 0)))
    sell_counts.append(int((trading.get("sell_count", perf.get("sell_count", 0)) or 0)))
    bot_states.append(s.get("bot_state"))
    live_flags.append(s.get("live_trading"))
    risk_statuses.append(s.get("risk_status"))

syntax_hits = count(r"SyntaxError")
traceback_hits = count(r"Traceback")
bot_loop_hits = count(r"Bot loop error")
sqlalchemy_hits = count(r"Not an executable object")
status_200_hits = count(r'GET /status HTTP/1\.1" 200 OK')

market_validated_hits = count(r"MARKET TICK DATA VALIDATED:\s*\{")
trusted_hits = count(r"trusted_count")
feature_symbols_hits = count(r"Feature symbols:\s*[1-9]")
normal_feature_hits = count(r"'source': 'NORMAL'|\"source\": \"NORMAL\"|normal_features['\"]?:\s*[1-9]|ready_features['\"]?:\s*[1-9]")
feature_empty_hits = count(r"FEATURES:\s*\{\}|Feature symbols:\s*0|Normal FEATURES is empty|FALLBACK FEATURES:\s*\{\}")

entry_top_empty_hits = count(r"ENTRY QUALITY TOP 10:\s*\[\]|QUALITY_RANK\] top-0")
entry_top_nonempty_hits = count(r"ENTRY QUALITY TOP 10:\s*\[\(")
allocator_rescue_hits = count(r"ALLOCATOR_RESCUE")
allocator_block_hits = count(r"ALLOCATOR BLOCK")
hard_exposure_gate_hits = count(r"hard_exposure_gate")
risk_block_hits = count(r"risk_status|risk gate|risk block|exposure gate|cooldown|recent_stop_loss")
raw_orders_nonzero_hits = count(r"raw_orders=[1-9]|ORDERS:\s*\[[^\]]+\]")
proposed_nonzero_hits = count(r"proposed_fills=[1-9]")
applied_nonzero_hits = count(r"final_applied_fills=[1-9]")

sell_reject_hits = count(r"SELL_VALIDATION_REJECT")
profit_engine_hits = count(r"PROFIT_ENGINE|profit_engine|sideways_max_hold_profit_engine|sideways_green_to_red_exit|adaptive_take_profit")
stale_reconciler_hits = count(r"stale.*reconcile|reconciler|qfos_reconcile_stale_closed_positions|stale position")
duplicate_guard_hits = count(r"duplicate_latest_sell|duplicate sell|DUPLICATE")

baseline_trades = baseline_db.get("trades_count")
final_trades = final_db.get("trades_count")
baseline_max_id = baseline_db.get("max_trade_id")
final_max_id = final_db.get("max_trade_id")

new_trades = None
if isinstance(baseline_trades, int) and isinstance(final_trades, int):
    new_trades = final_trades - baseline_trades

duplicate_sell_groups = final_db.get("duplicate_sell_groups") or []
negative_positions = final_db.get("negative_positions") or []
stale_open_positions = final_db.get("stale_open_positions") or []
open_positions = final_db.get("open_positions")
recent_trades = final_db.get("recent_trades") or []

final_equity = (final_status.get("portfolio") or {}).get("equity")
final_cash = (final_status.get("portfolio") or {}).get("cash")
final_exposure = (final_status.get("portfolio") or {}).get("exposure")
final_bot_state = final_status.get("bot_state")
final_live = final_status.get("live_trading")
final_positions_status_count = len(final_status.get("positions", []) or [])
final_perf = final_status.get("performance", {}) or {}
final_trading = final_status.get("trading", {}) or {}

final_buy_count = final_trading.get("buy_count", final_perf.get("buy_count"))
final_sell_count = final_trading.get("sell_count", final_perf.get("sell_count"))

# Classification
classification = "UNKNOWN"
owner = "None"
reason = ""

runtime_unhealthy = (
    syntax_hits > 0 or traceback_hits > 0 or bot_loop_hits > 0 or sqlalchemy_hits > 0
)

status_unhealthy = (
    final_bot_state != "RUNNING" or final_live is not False
)

execution_issue = (
    len(duplicate_sell_groups) > 0 or
    len(negative_positions) > 0 or
    len(stale_open_positions) > 0
)

sell_reject_storm = sell_reject_hits >= 60

if runtime_unhealthy:
    classification = "FAIL — RUNTIME ERROR"
    owner = "Agent 1"
    reason = "Runtime errors appeared in logs."
elif status_unhealthy:
    classification = "FAIL — API/RUNTIME STATUS"
    owner = "Agent 6" if final_bot_state is not None else "Agent 1"
    reason = "Final status was not RUNNING or live_trading was not false."
elif execution_issue:
    classification = "FAIL — EXECUTION SAFETY"
    owner = "Agent 5"
    reason = "Duplicate SELL groups, negative positions, or stale open positions detected."
elif sell_reject_storm:
    classification = "REVIEW — PROFIT ENGINE / EXECUTION REQUEST STORM"
    owner = "Agent 5"
    reason = "SELL validation protected DB, but repeated rejected SELL attempts indicate an upstream repeated-exit request."
elif new_trades and new_trades > 0:
    classification = "PASS — CLEAN RUN WITH TRADES"
    owner = "None"
    reason = "Trades occurred and accounting remained valid."
elif feature_empty_hits and not normal_feature_hits:
    classification = "BLOCKED — DATA/FEATURES"
    owner = "Agent 4"
    reason = "Features remained empty/unavailable after warmup."
elif normal_feature_hits and raw_orders_nonzero_hits == 0 and allocator_block_hits > 0:
    classification = "WAITING / POSSIBLE STRATEGY BLOCK"
    owner = "Agent 3"
    reason = "NORMAL features exist, but allocator repeatedly produced no valid positive strategy/orders."
elif normal_feature_hits and (raw_orders_nonzero_hits or proposed_nonzero_hits) and applied_nonzero_hits == 0:
    classification = "WAITING / POSSIBLE RISK OR QUALITY BLOCK"
    owner = "Agent 2 or Agent 3"
    reason = "Orders/proposals appeared, but no final applied fills."
else:
    classification = "PASS — CORRECTLY WAITING"
    owner = "None"
    reason = "Runtime stayed healthy, data/features available, no safety corruption, and no hard-block failure pattern."

# Compose report
report = []
report.append("# Agent 1 Phase 3A Clean Supervised Runtime Observation")
report.append("")
report.append("## Verdict")
report.append(f"- Clean-run verdict: {classification}")
report.append(f"- Owner if blocked: {owner}")
report.append(f"- Reason: {reason}")
report.append("")
report.append("## Baseline")
report.append(f"- baseline equity: {(baseline_status.get('portfolio') or {}).get('equity')}")
report.append(f"- baseline cash: {(baseline_status.get('portfolio') or {}).get('cash')}")
report.append(f"- baseline exposure: {(baseline_status.get('portfolio') or {}).get('exposure')}")
report.append(f"- baseline positions from status: {len(baseline_status.get('positions', []) or [])}")
report.append(f"- baseline DB trades count: {baseline_trades}")
report.append(f"- baseline DB max trade id: {baseline_max_id}")
report.append("")
report.append("## Final Status")
report.append(f"- final equity: {final_equity}")
report.append(f"- final cash: {final_cash}")
report.append(f"- final exposure: {final_exposure}")
report.append(f"- final positions from status: {final_positions_status_count}")
report.append(f"- final DB open positions: {open_positions}")
report.append(f"- final DB trades count: {final_trades}")
report.append(f"- new trades during window: {new_trades}")
report.append(f"- final buy_count: {final_buy_count}")
report.append(f"- final sell_count: {final_sell_count}")
report.append(f"- bot_state: {final_bot_state}")
report.append(f"- live_trading: {final_live}")
report.append("")
report.append("## Runtime Health")
report.append(f"- /status 200 hits: {status_200_hits}")
report.append(f"- SyntaxError hits: {syntax_hits}")
report.append(f"- Traceback hits: {traceback_hits}")
report.append(f"- Bot loop error hits: {bot_loop_hits}")
report.append(f"- SQLAlchemy Not executable hits: {sqlalchemy_hits}")
report.append("")
report.append("## Execution Safety")
report.append(f"- duplicate SELL groups: {duplicate_sell_groups if duplicate_sell_groups else 'NONE'}")
report.append(f"- negative positions: {negative_positions if negative_positions else 'NONE'}")
report.append(f"- stale open positions: {stale_open_positions if stale_open_positions else 'NONE'}")
report.append(f"- SELL_VALIDATION_REJECT hits: {sell_reject_hits}")
report.append(f"- duplicate guard hits: {duplicate_guard_hits}")
report.append("")
report.append("## Profit Engine / Reconciler")
report.append(f"- Profit Engine related hits: {profit_engine_hits}")
report.append(f"- stale reconciler related hits: {stale_reconciler_hits}")
report.append(f"- state table counts: {final_db.get('state_table_counts', {})}")
report.append("")
report.append("## Market Data / Features")
report.append(f"- MARKET TICK DATA VALIDATED hits: {market_validated_hits}")
report.append(f"- trusted_count hits: {trusted_hits}")
report.append(f"- Feature symbols positive hits: {feature_symbols_hits}")
report.append(f"- NORMAL/ready feature positive hits: {normal_feature_hits}")
report.append(f"- feature empty hits: {feature_empty_hits}")
report.append("")
report.append("## Strategy / Allocation")
report.append(f"- ENTRY QUALITY TOP 10 empty hits: {entry_top_empty_hits}")
report.append(f"- ENTRY QUALITY TOP 10 nonempty hits: {entry_top_nonempty_hits}")
report.append(f"- ALLOCATOR_RESCUE hits: {allocator_rescue_hits}")
report.append(f"- ALLOCATOR BLOCK hits: {allocator_block_hits}")
report.append(f"- hard_exposure_gate hits: {hard_exposure_gate_hits}")
report.append(f"- raw_orders nonzero hits: {raw_orders_nonzero_hits}")
report.append(f"- proposed_fills nonzero hits: {proposed_nonzero_hits}")
report.append(f"- final_applied_fills nonzero hits: {applied_nonzero_hits}")
report.append("")
report.append("## Trade Accounting")
if new_trades and new_trades > 0:
    report.append("- Trades occurred during the clean run.")
    report.append("- Buy/sell accounting is valid only if no duplicate SELL groups, no negative positions, and no stale open positions are listed above.")
else:
    report.append("- No trades occurred during the clean run.")
    if normal_feature_hits and allocator_block_hits:
        report.append("- Waiting appears driven by allocation/entry-quality conditions rather than data failure.")
    elif normal_feature_hits:
        report.append("- Waiting appears justified by no accepted final fills despite available features.")
    else:
        report.append("- Waiting may not be justified if feature generation remained unavailable.")
report.append("")
report.append("## Recent Trades")
if recent_trades:
    for r in recent_trades[:30]:
        report.append(f"- {r}")
else:
    report.append("- NONE")
report.append("")
report.append("## Evidence: Runtime / API")
for line in sample_lines(r"GET /status|SyntaxError|Traceback|Bot loop error|Not an executable object", 40):
    report.append(f"- {line}")
report.append("")
report.append("## Evidence: Market Data / Features")
for line in sample_lines(r"MARKET TICK DATA VALIDATED|trusted_count|Feature symbols|FEATURES:|Normal FEATURES|RAW_MOMENTUM_FALLBACK", 40):
    report.append(f"- {line}")
report.append("")
report.append("## Evidence: Entry Quality / Allocation")
for line in sample_lines(r"ENTRY QUALITY TOP 10|QUALITY_RANK|ALLOCATOR_RESCUE|ALLOCATOR BLOCK|raw_orders|proposed_fills|final_applied_fills|ORDERS:|STRATEGY SCORE DEBUG|EXECUTION_STAGE", 60):
    report.append(f"- {line}")
report.append("")
report.append("## Evidence: Execution Safety / Profit Engine / Reconciler")
for line in sample_lines(r"SELL_VALIDATION_REJECT|duplicate_latest_sell|duplicate sell|PROFIT_ENGINE|profit_engine|reconcile|reconciler|stale", 60):
    report.append(f"- {line}")

Path("phase3a_clean_runtime_report.md").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report))
