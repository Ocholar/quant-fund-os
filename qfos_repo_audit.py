from pathlib import Path
import re
import json
import sqlite3
from datetime import datetime

root = Path(".")
out = Path(".\\qfos_audit_20260602_074335")
report = []

def add(title, body=""):
    report.append("\n" + "="*90)
    report.append(title)
    report.append("="*90)
    if body:
        report.append(str(body))

def read_file(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"__READ_ERROR__ {e}"

def find_defs(text, names):
    rows = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for name in names:
            if re.search(rf"\b{name}\b", line):
                start = max(1, i-8)
                end = min(len(lines), i+25)
                snippet = "\n".join(f"{j:05d}: {lines[j-1]}" for j in range(start, end+1))
                rows.append((name, i, snippet))
    return rows

main = read_file("main.py")
add("MAIN.PY BASIC SIZE", f"chars={len(main)} lines={len(main.splitlines())}")

# Key symbols/functions to inspect
targets = [
    "OUTLIER_LOSS_CAP_SIDEWAYS_PCT",
    "_qfos_outlier_loss_exit_reason",
    "decision_hook_error",
    "_qfos_exit_decision",
    "adaptive_stop_loss",
    "adaptive_take_profit",
    "trailing_profit_exit",
    "breakeven_protection",
    "fallback_scout",
    "SCOUT_FALLBACK",
    "ENTRY QUALITY TOP 10",
    "build_entry_quality_top_symbols",
    "_entry_quality_reason",
    "_same_symbol_cooldown_reason",
    "_sideways_pacing_reason",
    "quarantine",
    "blocked_until",
    "cooldown",
    "stop_loss",
    "take_profit",
    "FULL_TAKE_PROFIT_PCT",
    "STOP_GRACE_MINUTES",
    "CATASTROPHIC_STOP_PCT",
    "_filter_and_resize_orders",
    "max_entry_notional",
]

matches = find_defs(main, targets)
for name, line, snippet in matches:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    p = out / f"main_match_{safe_name}_{line}.txt"
    p.write_text(snippet, encoding="utf-8")

add("MAIN.PY MATCH SUMMARY", "\n".join(f"{name} at line {line}" for name, line, _ in matches[:300]))

# Inspect config JSON files
for cfg in ["qfos_expectancy_config.json", "winning_strategy_config.json"]:
    p = Path(cfg)
    if p.exists():
        txt = read_file(p)
        add(f"CONFIG: {cfg}", txt[:6000])

# Inspect recent expectancy decisions
p = Path("qfos_expectancy_decisions.jsonl")
if p.exists():
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    add("EXPECTANCY LOG LAST 80", "\n".join(lines[-80:]))

# Inspect Docker log tail
log_path = out / "docker_tail_2500.log"
if log_path.exists():
    log = log_path.read_text(encoding="utf-8", errors="replace")
    interesting = []
    patterns = [
        "OUTLIER_LOSS_CAP",
        "adaptive_stop_loss",
        "adaptive_take_profit",
        "trailing_profit_exit",
        "breakeven",
        "risk_off_exit",
        "SCOUT_FALLBACK",
        "ENTRY QUALITY TOP 10",
        "entry_quality_not_top_10",
        "cooldown",
        "quarantine",
        "ORDERS:",
        "EXPECTANCY_PATCH",
        "Bot loop error",
    ]
    for line in log.splitlines():
        if any(pat in line for pat in patterns):
            interesting.append(line)
    add("INTERESTING DOCKER LOG LINES", "\n".join(interesting[-500:]))

# SQLite DB inspection
db_candidates = [
    Path("data/quant.db"),
    Path(out / "quant.db")
]

db_path = None
for p in db_candidates:
    if p.exists():
        db_path = p
        break

if db_path:
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        add("SQLITE TABLES", "\n".join(tables))

        for table in tables:
            try:
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
                add(f"TABLE SCHEMA: {table}", ", ".join(cols))
            except Exception as e:
                add(f"TABLE SCHEMA ERROR: {table}", str(e))

        likely_trade_tables = [t for t in tables if any(k in t.lower() for k in ["trade", "order", "position", "quarantine", "cooldown", "symbol"])]
        for t in likely_trade_tables:
            try:
                rows = cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 30").fetchall()
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
                body = "COLUMNS: " + ", ".join(cols) + "\n"
                for row in rows:
                    body += repr(row) + "\n"
                add(f"RECENT ROWS: {t}", body)
            except Exception as e:
                add(f"RECENT ROWS ERROR: {t}", str(e))

        # Trade expectancy summary if a trades table exists
        for t in tables:
            if "trade" in t.lower():
                cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
                pnl_cols = [c for c in cols if "pnl" in c.lower() or "profit" in c.lower()]
                symbol_cols = [c for c in cols if c.lower() == "symbol"]
                reason_cols = [c for c in cols if "reason" in c.lower() or "strategy" in c.lower() or "exit" in c.lower()]
                if pnl_cols:
                    pnl = pnl_cols[0]
                    try:
                        summary = cur.execute(
                            f"""
                            SELECT
                                COUNT(*) AS n,
                                SUM(CASE WHEN {pnl} > 0 THEN 1 ELSE 0 END) AS wins,
                                SUM(CASE WHEN {pnl} < 0 THEN 1 ELSE 0 END) AS losses,
                                AVG(CASE WHEN {pnl} > 0 THEN {pnl} END) AS avg_win,
                                AVG(CASE WHEN {pnl} < 0 THEN {pnl} END) AS avg_loss,
                                MIN({pnl}) AS worst_loss,
                                MAX({pnl}) AS best_win,
                                SUM({pnl}) AS total_pnl
                            FROM {t}
                            """
                        ).fetchone()
                        add(f"PNL SUMMARY FROM {t}", repr(summary))

                        rows = cur.execute(f"SELECT * FROM {t} ORDER BY {pnl} ASC LIMIT 15").fetchall()
                        body = "COLUMNS: " + ", ".join(cols) + "\n"
                        for row in rows:
                            body += repr(row) + "\n"
                        add(f"WORST 15 TRADES FROM {t}", body)
                    except Exception as e:
                        add(f"PNL SUMMARY ERROR FROM {t}", str(e))

        conn.close()
    except Exception as e:
        add("SQLITE INSPECTION ERROR", str(e))
else:
    add("SQLITE", "No quant.db found.")

# Write final report
(out / "AUDIT_REPORT.txt").write_text("\n".join(report), encoding="utf-8")
print(f"AUDIT_DONE: {out / 'AUDIT_REPORT.txt'}")
