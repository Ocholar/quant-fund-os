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

# Simulate corrupted/stale state:
# latest trade is SELL, but position still has open quantity.
cur.execute("""
INSERT INTO positions(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, updated_at)
VALUES('XMR/USDT', 0.00431100195215211, 368.31, -0.021, -0.05, 354.6, 1.52, 'sideways_hard_exposure_guard', 'now')
""")

cur.execute("""
INSERT INTO trades(symbol, side, quantity, expected_price, fill_price, slippage_bps, pnl, strategy, confidence, live, shadow_mode, source, created_at)
VALUES('XMR/USDT', 'sell', 0.00431100195215211, 368.0, 368.0, 0.0, -0.001, 'sideways_hard_exposure_guard', 1.0, 0, 0, 'test', 'now')
""")

before_trades = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

result = persist(conn, {
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
}, source="phase2e_test")

after_trades = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
qty, exposure, unrealized = cur.execute("""
SELECT quantity, exposure, unrealized_pnl
FROM positions
WHERE symbol='XMR/USDT'
""").fetchone()

assert result is None
assert before_trades == after_trades
assert abs(float(qty)) < 1e-12
assert abs(float(exposure)) < 1e-12
assert abs(float(unrealized)) < 1e-12

print("PASS: duplicate latest SELL reconciled stale open position to zero with no new trade row")
print("ALL_PHASE2E_RECONCILIATION_TESTS_PASSED")
