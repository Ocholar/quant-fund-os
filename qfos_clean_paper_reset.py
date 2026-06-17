import sqlite3, datetime, json, os, sys

DB = "/app/data/quant.db"

if not os.path.exists(DB):
    raise SystemExit(f"DB_NOT_FOUND: {DB}")

conn = sqlite3.connect(DB)
cur = conn.cursor()

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def table_exists(name):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone() is not None

def cols(table):
    if not table_exists(table):
        return []
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

def clear_table(name):
    if table_exists(name):
        cur.execute(f"DELETE FROM {name}")
        print(f"CLEARED_TABLE: {name}")
        return True
    print(f"TABLE_NOT_PRESENT: {name}")
    return False

def insert_clean_portfolio_baseline():
    """
    Reset snapshots to a clean paper baseline:
    equity=100, cash=100, exposure=0, drawdown=0, regime=SIDEWAYS.
    Handles schema differences safely by inserting only columns that exist.
    """
    if not table_exists("portfolio_snapshots"):
        print("TABLE_NOT_PRESENT: portfolio_snapshots")
        return

    clear_table("portfolio_snapshots")

    c = cols("portfolio_snapshots")

    data = {
        "equity": 100.0,
        "cash": 100.0,
        "exposure": 0.0,
        "exposure_pct": 0.0,
        "drawdown": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
        "regime": "SIDEWAYS",
        "created_at": now,
        "updated_at": now,
        "timestamp": now,
        "time": now,
    }

    insert_cols = [k for k in data.keys() if k in c]

    if not insert_cols:
        print("PORTFOLIO_BASELINE_INSERT_SKIPPED: no matching columns")
        return

    placeholders = ",".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO portfolio_snapshots ({','.join(insert_cols)}) VALUES ({placeholders})"
    cur.execute(sql, [data[k] for k in insert_cols])
    print("INSERTED_CLEAN_PORTFOLIO_BASELINE: portfolio_snapshots")

def reset_stateful_portfolio_like_tables():
    """
    Some builds may keep current portfolio/account values outside portfolio_snapshots.
    Reset only obvious paper-account fields if those tables exist.
    """
    for table in ["portfolio", "account", "accounts", "bot_state", "runtime_state"]:
        if not table_exists(table):
            continue

        c = cols(table)
        updates = {}

        for field, value in {
            "equity": 100.0,
            "cash": 100.0,
            "exposure": 0.0,
            "exposure_pct": 0.0,
            "drawdown": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "updated_at": now,
        }.items():
            if field in c:
                updates[field] = value

        if updates:
            set_sql = ", ".join([f"{k}=?" for k in updates.keys()])
            cur.execute(f"UPDATE {table} SET {set_sql}", list(updates.values()))
            print(f"RESET_PORTFOLIO_FIELDS: {table}")

def ensure_live_trading_false():
    """
    Do not enable live trading. If config tables exist, force live flags false.
    """
    for table in ["settings", "config", "bot_config", "runtime_config"]:
        if not table_exists(table):
            continue

        c = cols(table)

        if "key" in c and "value" in c:
            cur.execute(
                f"""
                UPDATE {table}
                SET value='false'
                WHERE lower(key) IN (
                    'live_trading',
                    'live',
                    'enable_live_trading',
                    'LIVE_TRADING'
                )
                """
            )
            print(f"ENSURED_LIVE_FALSE_KEY_VALUE: {table}")

        for live_col in ["live_trading", "live", "enable_live_trading"]:
            if live_col in c:
                cur.execute(f"UPDATE {table} SET {live_col}=0")
                print(f"ENSURED_LIVE_FALSE_COLUMN: {table}.{live_col}")

def count_rows(table):
    if not table_exists(table):
        return None
    return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

print("=== RESET START ===")

# Paper trading ledger reset
clear_table("trades")
clear_table("positions")

# Portfolio baseline
insert_clean_portfolio_baseline()
reset_stateful_portfolio_like_tables()

# Runtime/profit/quarantine/reconciler state cleanup
for table in [
    "profit_engine_state",
    "profit_state",
    "position_peak_state",
    "strategy_quarantine",
    "symbol_quarantine",
    "quarantine",
    "symbol_cooldowns",
    "stale_reconciler_state",
    "reconciler_state",
    "closed_position_reconciler_state",
]:
    clear_table(table)

# Keep live trading disabled
ensure_live_trading_false()

conn.commit()

print("=== POST-RESET DB VERIFY ===")

summary = {}

for table in [
    "trades",
    "positions",
    "portfolio_snapshots",
    "profit_engine_state",
    "position_peak_state",
    "symbol_quarantine",
    "strategy_quarantine",
    "stale_reconciler_state",
    "reconciler_state",
]:
    if table_exists(table):
        summary[table] = count_rows(table)

summary["negative_positions"] = 0
summary["open_positions"] = 0
summary["duplicate_sell_exact_patterns"] = 0

if table_exists("positions") and "quantity" in cols("positions"):
    summary["negative_positions"] = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE quantity < -0.00000001"
    ).fetchone()[0]

    summary["open_positions"] = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001"
    ).fetchone()[0]

if table_exists("trades"):
    trade_cols = cols("trades")
    if all(x in trade_cols for x in ["symbol", "side", "quantity", "strategy", "created_at"]):
        summary["duplicate_sell_exact_patterns"] = cur.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT symbol, side, quantity, strategy, created_at, COUNT(*) AS n
                FROM trades
                WHERE lower(side)='sell'
                GROUP BY symbol, side, quantity, strategy, created_at
                HAVING n > 1
            )
        """).fetchone()[0]

print(json.dumps(summary, indent=2, sort_keys=True))

conn.close()

print("=== RESET COMPLETE ===")
