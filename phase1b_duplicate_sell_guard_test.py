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
    raise SystemExit("FAIL: atomic block not found")

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

def tc():
    return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

def sc():
    return cur.execute("SELECT COUNT(*) FROM trades WHERE side='sell'").fetchone()[0]

def qty(sym):
    row = cur.execute("SELECT quantity FROM positions WHERE symbol=?", (sym,)).fetchone()
    return 0.0 if not row else float(row[0] or 0.0)

def buy(sym, q, px=1.0):
    return persist(conn, {
        "symbol": sym,
        "side": "buy",
        "quantity": q,
        "expected_price": px,
        "fill_price": px,
        "slippage_bps": 0.0,
        "strategy": "test_buy",
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase1b_test")

def sell(sym, q, px=1.2, strategy="sideways_max_hold_profit_engine"):
    return persist(conn, {
        "symbol": sym,
        "side": "sell",
        "quantity": q,
        "expected_price": px,
        "fill_price": px,
        "slippage_bps": 0.0,
        "strategy": strategy,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase1b_test")

assert buy("ATLA/USDT", 0.007661512347936049, 195.0) is not None
assert tc() == 1
print("PASS 1: buy persisted")

first = sell("ATLA/USDT", 0.007661512347936049, 194.5)
assert first is not None
assert tc() == 2
assert sc() == 1
print("PASS 2: first full sell persisted")

repeat = sell("ATLA/USDT", 0.007661512347936049, 194.4)
assert repeat is None
assert tc() == 2
assert sc() == 1
print("PASS 3: duplicate same-symbol same-qty same-strategy sell rejected with no trade row")

assert sell("ATLA/USDT", 0.007661512347936049, 194.3, strategy="different_exit") is None
assert tc() == 2
assert sc() == 1
print("PASS 4: repeated sell after zero rejected even with different strategy")

neg = list(cur.execute("SELECT symbol, quantity FROM positions WHERE quantity < -0.00000001"))
assert neg == []
print("PASS 5: no negative positions")

print("ALL_PHASE1B_DUPLICATE_SELL_GUARD_TESTS_PASSED")
