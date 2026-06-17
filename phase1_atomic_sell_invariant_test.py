import sqlite3
import re
from pathlib import Path

source = Path("main.py").read_text(encoding="utf-8")
m = re.search(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    source,
    flags=re.S,
)

if not m:
    raise SystemExit("FAIL: canonical atomic boundary block not found")

ns = {}
exec(m.group(0), ns)

qfos_persist_fill_atomic = ns["qfos_persist_fill_atomic"]

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

def trade_count():
    return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

def sell_count():
    return cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell'").fetchone()[0]

def pos_qty(symbol):
    row = cur.execute("SELECT quantity FROM positions WHERE symbol=?", (symbol,)).fetchone()
    return 0.0 if not row else float(row[0] or 0.0)

def avg_entry(symbol):
    row = cur.execute("SELECT avg_entry FROM positions WHERE symbol=?", (symbol,)).fetchone()
    return 0.0 if not row else float(row[0] or 0.0)

def buy(symbol, qty, price=1.0):
    return qfos_persist_fill_atomic(conn, {
        "symbol": symbol,
        "side": "buy",
        "quantity": qty,
        "expected_price": price,
        "fill_price": price,
        "slippage_bps": 0.0,
        "strategy": "phase1_test_buy",
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase1_test")

def sell(symbol, qty, price=1.2):
    return qfos_persist_fill_atomic(conn, {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": price,
        "fill_price": price,
        "slippage_bps": 0.0,
        "strategy": "sideways_green_to_red_exit",
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase1_test")

assert sell("UNKNOWN/USDT", 1.0) is None
assert trade_count() == 0
assert sell_count() == 0
print("PASS 1: SELL unknown/no-open position rejected, no trade row")

assert buy("EDEN/USDT", 1.0, 1.0) is not None
assert abs(pos_qty("EDEN/USDT") - 1.0) < 1e-12
assert abs(avg_entry("EDEN/USDT") - 1.0) < 1e-12
assert trade_count() == 1
print("PASS 2: BUY 1 EDEN creates position quantity 1")

assert sell("EDEN/USDT", 0.0, 1.2) is None
assert abs(pos_qty("EDEN/USDT") - 1.0) < 1e-12
assert trade_count() == 1
assert sell_count() == 0
print("PASS 3: SELL requested_qty <= 0 rejected, no trade row")

oversell = sell("EDEN/USDT", 2.0, 1.2)
assert oversell is not None
assert abs(float(oversell["quantity"]) - 1.0) < 1e-12
assert abs(pos_qty("EDEN/USDT") - 0.0) < 1e-12
assert trade_count() == 2
assert sell_count() == 1
print("PASS 4: SELL qty > open_qty capped to open quantity, no negative position")

assert sell("EDEN/USDT", 1.0, 1.2) is None
assert abs(pos_qty("EDEN/USDT") - 0.0) < 1e-12
assert trade_count() == 2
assert sell_count() == 1
print("PASS 5: repeated full-position SELL after zero rejected, no trade row")

pnl = cur.execute("SELECT pnl FROM trades WHERE side='sell' ORDER BY id DESC LIMIT 1").fetchone()[0]
assert abs(float(pnl) - 0.2) < 1e-9
print("PASS 6: realized PnL calculated from DB avg_entry")

neg = list(cur.execute("SELECT symbol, quantity FROM positions WHERE quantity < -0.00000001"))
assert neg == []
print("PASS 7: no negative positions")

print("ALL_PHASE1_ATOMIC_SELL_INVARIANTS_PASSED")
