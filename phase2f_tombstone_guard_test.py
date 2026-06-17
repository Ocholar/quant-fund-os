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

def persist_fill(fill, source="phase2f_test"):
    return persist(conn, fill, source=source)

buy = {
    "symbol": "ETHFI/USDT", "side": "buy", "quantity": 5.775966592382718,
    "expected_price": 0.3448, "fill_price": 0.3448,
    "slippage_bps": 0.0, "strategy": "test_buy",
    "confidence": 1.0, "live": False, "shadow_mode": False,
}
sell_a = {
    "symbol": "ETHFI/USDT", "side": "sell", "quantity": 5.775966592382718,
    "expected_price": 0.3323, "fill_price": 0.3323,
    "slippage_bps": 0.0, "strategy": "sideways_hard_exposure_guard",
    "confidence": 1.0, "live": False, "shadow_mode": False,
}
sell_b = dict(sell_a)
sell_b["strategy"] = "basket_loss_cap"
sell_b["fill_price"] = 0.3319
sell_b["expected_price"] = 0.3319

assert persist_fill(buy) is not None
assert persist_fill(sell_a, source="profit_engine") is not None

# Simulate stale paper_position_sync restoring ETHFI as open after full SELL.
cur.execute("""
UPDATE positions
SET quantity=?, exposure=?, unrealized_pnl=?, strategy=?
WHERE symbol='ETHFI/USDT'
""", (5.775966592382718, 1.92, -0.07, "paper_position_sync"))

before_trades = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
assert persist_fill(sell_b, source="emergency_basket_watchdog") is None
after_trades = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

qty, exposure, unrealized = cur.execute("""
SELECT quantity, exposure, unrealized_pnl
FROM positions
WHERE symbol='ETHFI/USDT'
""").fetchone()

assert before_trades == after_trades
assert abs(float(qty)) < 1e-12
assert abs(float(exposure)) < 1e-12
assert abs(float(unrealized)) < 1e-12

print("PASS: tombstone rejected second SELL and rezeroed stale restored position with no trade row")
print("ALL_PHASE2F_TOMBSTONE_TESTS_PASSED")
