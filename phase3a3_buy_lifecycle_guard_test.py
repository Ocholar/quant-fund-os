import sqlite3
import re
from pathlib import Path

s = Path("main.py").read_text(encoding="utf-8-sig")
m = re.search(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
)
if not m:
    raise SystemExit("FAIL: atomic block missing")

ns = {}
exec(m.group(0), ns)
persist = ns["qfos_persist_fill_atomic"]
reconcile_no_buy = ns["qfos_reconcile_positions_without_buy_lifecycle"]

conn = sqlite3.connect(":memory:")
cur = conn.cursor()

cur.execute("""
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL,
    avg_entry REAL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    last_price REAL,
    exposure REAL,
    strategy TEXT,
    updated_at TEXT
)
""")

cur.execute("""
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    side TEXT,
    quantity REAL,
    expected_price REAL,
    fill_price REAL,
    slippage_bps REAL,
    pnl REAL,
    strategy TEXT,
    confidence REAL,
    live BOOLEAN,
    shadow_mode BOOLEAN,
    source TEXT,
    is_exit BOOLEAN,
    exit_reason TEXT,
    created_at TEXT
)
""")

def tc():
    return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

def qty(sym):
    row = cur.execute("SELECT quantity FROM positions WHERE symbol=?", (sym,)).fetchone()
    return 0.0 if not row else float(row[0] or 0.0)

# Simulate paper_position_sync after clean reset.
cur.execute("""
INSERT INTO positions(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, updated_at)
VALUES('HYPE/USDT', 0.02987875884108635, 67.05, 0.0, -0.01, 66.87, 1.99, 'paper_position_sync', 'now')
""")

sell = {
    "symbol": "HYPE/USDT",
    "side": "sell",
    "quantity": 0.02987875884108635,
    "expected_price": 66.87,
    "fill_price": 66.87,
    "slippage_bps": 0.0,
    "strategy": "sideways_hard_exposure_guard",
    "confidence": 1.0,
    "live": False,
    "shadow_mode": False,
}

result = persist(conn, sell, source="phase3a3_test")
assert result is None
assert tc() == 0
assert abs(qty("HYPE/USDT")) < 1e-12
print("PASS 1: SELL with no BUY lifecycle rejected, no trade row, stale position zeroed")

cur.execute("""
INSERT OR REPLACE INTO positions(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, updated_at)
VALUES('NEAR/USDT', 0.8534658090688578, 2.3187, 0.0, 0.01, 2.3291, 1.99, 'paper_position_sync', 'now')
""")

fixed = reconcile_no_buy(conn, source="phase3a3_test")
assert "NEAR/USDT" in fixed
assert abs(qty("NEAR/USDT")) < 1e-12
assert tc() == 0
print("PASS 2: no-BUY lifecycle reconciler zeroes restored position with no trade row")

print("ALL_PHASE3A3_BUY_LIFECYCLE_GUARD_TESTS_PASSED")
