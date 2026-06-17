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
    is_exit BOOLEAN,
    exit_reason TEXT,
    created_at TEXT
)
""")

def count_trades():
    return cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

def side_count(side):
    return cur.execute("SELECT COUNT(*) FROM trades WHERE side=?", (side,)).fetchone()[0]

def qty(symbol):
    row = cur.execute("SELECT quantity FROM positions WHERE symbol=?", (symbol,)).fetchone()
    return 0.0 if not row else float(row[0] or 0.0)

def buy(symbol, q=1.0, px=1.0):
    return persist(conn, {
        "symbol": symbol,
        "side": "buy",
        "quantity": q,
        "expected_price": px,
        "fill_price": px,
        "slippage_bps": 0.0,
        "strategy": "phase3a_test_buy",
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase3a_test")

def sell(symbol, q=1.0, px=1.1, strategy="sideways_hard_exposure_guard"):
    return persist(conn, {
        "symbol": symbol,
        "side": "sell",
        "quantity": q,
        "expected_price": px,
        "fill_price": px,
        "slippage_bps": 0.0,
        "strategy": strategy,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
    }, source="phase3a_test")

# 1. Sell unknown symbol -> rejected, no trade row.
assert sell("UNKNOWN/USDT", 1.0) is None
assert count_trades() == 0
print("PASS 1: unknown SELL rejected with no trade row")

# 2. Sell with zero open quantity -> rejected, no trade row.
cur.execute("""
INSERT INTO positions(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, updated_at)
VALUES('ZERO/USDT', 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 'test', 'now')
""")
assert sell("ZERO/USDT", 1.0) is None
assert count_trades() == 0
print("PASS 2: zero-quantity SELL rejected with no trade row")

# 3. Buy 1 TEST.
assert buy("TEST/USDT", 1.0, 1.0) is not None
assert abs(qty("TEST/USDT") - 1.0) < 1e-12
assert count_trades() == 1
assert side_count("buy") == 1
print("PASS 3: BUY persisted correctly")

# 4. Sell 1 TEST -> is_exit true, exit_reason populated.
assert sell("TEST/USDT", 1.0, 1.1, "sideways_hard_exposure_guard") is not None
assert abs(qty("TEST/USDT")) < 1e-12
assert count_trades() == 2
assert side_count("sell") == 1

row = cur.execute("""
SELECT is_exit, exit_reason, strategy
FROM trades
WHERE side='sell'
ORDER BY id DESC
LIMIT 1
""").fetchone()

assert row is not None
assert int(row[0]) == 1
assert row[1] not in (None, "", "None")
assert row[1] == "sideways_hard_exposure_guard"
print("PASS 4: protective SELL persisted as exit with exit_reason")

# 5. Second full sell rejected.
assert sell("TEST/USDT", 1.0, 1.1, "sideways_hard_exposure_guard") is None
assert count_trades() == 2
assert side_count("sell") == 1
print("PASS 5: duplicate full SELL rejected with no extra row")

# 6. Oversell capped/rejected, never negative.
assert buy("OVER/USDT", 1.0, 1.0) is not None
before = count_trades()
res = sell("OVER/USDT", 2.0, 1.1, "sideways_hard_exposure_guard")
assert res is not None
assert float(res["quantity"]) <= 1.0 + 1e-12
assert qty("OVER/USDT") >= -1e-12
assert count_trades() == before + 1
print("PASS 6: oversell safely capped and no negative position")

neg = list(cur.execute("SELECT symbol, quantity FROM positions WHERE quantity < -0.00000001"))
assert neg == []
print("PASS 7: no negative positions")

print("ALL_PHASE3A_EXECUTION_ACCOUNTING_TESTS_PASSED")
