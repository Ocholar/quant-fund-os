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
cleanup = ns["_qfos_cleanup_closed_symbol_runtime_state"]

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
    created_at TEXT
)
""")

# Simulate global Profit Engine caches.
ns["profit_engine_state"] = {"XMR/USDT": {"peak": 1.0}}
ns["profit_engine_peaks"] = {"XMR/USDT": 999.0}

def call(fill):
    old_globals = persist.__globals__
    old_globals["profit_engine_state"] = ns["profit_engine_state"]
    old_globals["profit_engine_peaks"] = ns["profit_engine_peaks"]
    return persist(conn, fill, source="phase2d_test")

buy = {
    "symbol": "XMR/USDT",
    "side": "buy",
    "quantity": 0.00431100195215211,
    "expected_price": 368.31,
    "fill_price": 368.31,
    "slippage_bps": 0.0,
    "strategy": "test_buy",
    "confidence": 1.0,
    "live": False,
    "shadow_mode": False,
}

sell = {
    "symbol": "XMR/USDT",
    "side": "sell",
    "quantity": 0.00431100195215211,
    "expected_price": 368.0,
    "fill_price": 368.0,
    "slippage_bps": 0.0,
    "strategy": "sideways_hard_exposure_guard",
    "confidence": 1.0,
    "live": False,
    "shadow_mode": False,
}

assert call(buy) is not None
assert call(sell) is not None

repeat = call(sell)
assert repeat is None

assert "XMR/USDT" not in ns["profit_engine_state"]
assert "XMR/USDT" not in ns["profit_engine_peaks"]

trade_count = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
sell_count = cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell'").fetchone()[0]
assert trade_count == 2
assert sell_count == 1

print("PASS: duplicate_latest_sell clears profit engine state and creates no duplicate trade row")
print("ALL_PHASE2D_SELL_STORM_SOURCE_TESTS_PASSED")
