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
        "symbol": sym, "side": "buy", "quantity": q,
        "expected_price": px, "fill_price": px,
        "slippage_bps": 0.0, "strategy": "test_buy",
        "confidence": 1.0, "live": False, "shadow_mode": False,
    }, source="final_phase1_test")

def sell(sym, q, px=1.2):
    return persist(conn, {
        "symbol": sym, "side": "sell", "quantity": q,
        "expected_price": px, "fill_price": px,
        "slippage_bps": 0.0, "strategy": "sideways_green_to_red_exit",
        "confidence": 1.0, "live": False, "shadow_mode": False,
    }, source="final_phase1_test")

assert sell("UNKNOWN/USDT", 1.0) is None
assert tc() == 0 and sc() == 0
print("PASS 1: unknown/no-open sell rejected")

assert buy("EDEN/USDT", 1.0, 1.0) is not None
assert abs(qty("EDEN/USDT") - 1.0) < 1e-12
assert tc() == 1
print("PASS 2: buy creates open quantity")

assert sell("EDEN/USDT", 0.0, 1.2) is None
assert abs(qty("EDEN/USDT") - 1.0) < 1e-12
assert tc() == 1 and sc() == 0
print("PASS 3: zero sell rejected")

over = sell("EDEN/USDT", 2.0, 1.2)
assert over is not None
assert abs(float(over["quantity"]) - 1.0) < 1e-12
assert abs(qty("EDEN/USDT")) < 1e-12
assert tc() == 2 and sc() == 1
print("PASS 4: oversell capped safely")

assert sell("EDEN/USDT", 1.0, 1.2) is None
assert abs(qty("EDEN/USDT")) < 1e-12
assert tc() == 2 and sc() == 1
print("PASS 5: repeated full sell rejected")

pnl = cur.execute("SELECT pnl FROM trades WHERE side='sell' ORDER BY id DESC LIMIT 1").fetchone()[0]
assert abs(float(pnl) - 0.2) < 1e-9
print("PASS 6: PnL uses DB avg_entry")

neg = list(cur.execute("SELECT symbol, quantity FROM positions WHERE quantity < -0.00000001"))
assert neg == []
print("PASS 7: no negative positions")

print("ALL_PHASE1_ATOMIC_SELL_INVARIANTS_PASSED")
