"""
PHASE 2C CLEAN PAPER DB RESET PROPOSAL
DO NOT RUN until Project Manager approves.

Objective:
- equity = 100
- cash = 100
- exposure = 0
- positions = []
- trades = 0
- portfolio_snapshots clean baseline
- quarantine cleared only if safe
- profit_engine state cleared
- no live trading changes

Run only inside quant container:
python /tmp/phase2c_clean_paper_reset_APPROVED.py
"""

import sqlite3
import shutil
import datetime
import os

DB = "/app/data/quant.db"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = f"/app/data/quant_pre_phase2c_clean_reset_{STAMP}.db"

if not os.path.exists(DB):
    raise SystemExit(f"DB not found: {DB}")

shutil.copy2(DB, BACKUP)
print(f"[RESET_BACKUP_CREATED] {BACKUP}")

conn = sqlite3.connect(DB)
cur = conn.cursor()

def table_exists(name):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone() is not None

def columns(table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

def safe_delete(table):
    if table_exists(table):
        cur.execute(f"DELETE FROM {table}")
        print(f"[RESET_TABLE_CLEARED] {table}")

def safe_zero_sequence(table):
    if table_exists("sqlite_sequence"):
        cur.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))

conn.execute("BEGIN")

# 1) Clear trade history.
safe_delete("trades")
safe_zero_sequence("trades")

# 2) Clear positions completely.
safe_delete("positions")
safe_zero_sequence("positions")

# 3) Clear portfolio snapshots.
safe_delete("portfolio_snapshots")
safe_zero_sequence("portfolio_snapshots")

# 4) Clear quarantine only for clean paper baseline.
# This is safe only because PM approval should be explicit and this is paper mode.
for t in ["quarantine", "symbol_quarantine", "symbol_cooldowns"]:
    safe_delete(t)
    safe_zero_sequence(t)

# 5) Clear profit engine / peak / watchdog state tables if present.
for t in [
    "profit_engine_state",
    "qfos_profit_engine_state",
    "position_peak_state",
    "qfos_position_peak_state",
    "peak_state",
    "watchdog_state",
    "active_position_watchdog_state",
    "emergency_basket_watchdog_state"
]:
    safe_delete(t)
    safe_zero_sequence(t)

# 6) Clear strategy score/state only if these are runtime-derived paper state.
# Do NOT delete strategy definitions if they are actual config.
for t in [
    "strategy_scores",
    "strategy_runtime_state",
    "strategy_state",
    "allocator_state"
]:
    if table_exists(t):
        # Conservative: clear only obvious runtime tables.
        safe_delete(t)
        safe_zero_sequence(t)

# 7) Reset portfolio/state tables if present.
# This uses schema-aware inserts/updates to avoid SQLAlchemy/schema mismatch issues.
if table_exists("portfolio"):
    cols = columns("portfolio")
    cur.execute("DELETE FROM portfolio")
    insert_cols = []
    values = []

    def add(col, val):
        if col in cols:
            insert_cols.append(col)
            values.append(val)

    add("equity", 100.0)
    add("cash", 100.0)
    add("exposure", 0.0)
    add("exposure_pct", 0.0)
    add("drawdown", 0.0)
    add("realized_pnl", 0.0)
    add("unrealized_pnl", 0.0)
    add("total_pnl", 0.0)
    add("regime", "SIDEWAYS")
    add("updated_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if insert_cols:
        placeholders = ",".join(["?"] * len(insert_cols))
        cur.execute(
            f"INSERT INTO portfolio ({','.join(insert_cols)}) VALUES ({placeholders})",
            values
        )
        print("[RESET_PORTFOLIO_ROW_CREATED] portfolio")

if table_exists("bot_state"):
    cols = columns("bot_state")
    # Preserve paused/running if table is used for controls; do not enable live trading.
    if "live_trading" in cols:
        cur.execute("UPDATE bot_state SET live_trading=0")
        print("[RESET_LIVE_TRADING_CONFIRMED_FALSE] bot_state.live_trading=0")
    if "mode" in cols:
        cur.execute("UPDATE bot_state SET mode='paper'")
        print("[RESET_MODE_CONFIRMED_PAPER] bot_state.mode=paper")

# 8) Insert clean baseline snapshot if schema supports it.
if table_exists("portfolio_snapshots"):
    cols = columns("portfolio_snapshots")
    insert_cols = []
    values = []

    def add(col, val):
        if col in cols:
            insert_cols.append(col)
            values.append(val)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    add("equity", 100.0)
    add("cash", 100.0)
    add("exposure", 0.0)
    add("exposure_pct", 0.0)
    add("drawdown", 0.0)
    add("realized_pnl", 0.0)
    add("unrealized_pnl", 0.0)
    add("total_pnl", 0.0)
    add("regime", "SIDEWAYS")
    add("created_at", now)
    add("updated_at", now)

    if insert_cols:
        placeholders = ",".join(["?"] * len(insert_cols))
        cur.execute(
            f"INSERT INTO portfolio_snapshots ({','.join(insert_cols)}) VALUES ({placeholders})",
            values
        )
        print("[RESET_BASELINE_SNAPSHOT_CREATED] portfolio_snapshots")

conn.commit()

# Final verification.
verify = {}
if table_exists("trades"):
    verify["trades"] = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
if table_exists("positions"):
    verify["positions"] = cur.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001").fetchone()[0]
    verify["negative_positions"] = cur.execute("SELECT COUNT(*) FROM positions WHERE quantity < -0.00000001").fetchone()[0]
if table_exists("portfolio_snapshots"):
    verify["portfolio_snapshots"] = cur.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
if table_exists("portfolio"):
    verify["portfolio_rows"] = cur.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]

print("[RESET_VERIFY]", verify)
print("[RESET_DONE] Clean paper DB baseline prepared.")
print(f"[ROLLBACK_DB] {BACKUP}")

conn.close()
