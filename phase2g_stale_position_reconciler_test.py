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
reconcile = ns["qfos_reconcile_stale_closed_positions"]

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

cur.execute("""
INSERT INTO positions(symbol, quantity, avg_entry, realized_pnl, unrealized_pnl, last_price, exposure, strategy, updated_at)
VALUES('ETHFI/USDT', 5.775966592382718, 0.3448, -4.29, -0.05, 0.3361, 1.94, 'basket_loss_cap', 'now')
""")

cur.execute("""
INSERT INTO trades(symbol, side, quantity, expected_price, fill_price, slippage_bps, pnl, strategy, confidence, live, shadow_mode, source, created_at)
VALUES('ETHFI/USDT', 'sell', 5.775966592382718, 0.3323, 0.3323, 0.0, -0.07, 'basket_loss_cap', 1.0, 0, 0, 'test', 'now')
""")

before = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
symbols = reconcile(conn, source="phase2g_test")
after = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

qty, exposure, unrealized = cur.execute("""
SELECT quantity, exposure, unrealized_pnl
FROM positions
WHERE symbol='ETHFI/USDT'
""").fetchone()

assert "ETHFI/USDT" in symbols
assert before == after
assert abs(float(qty)) < 1e-12
assert abs(float(exposure)) < 1e-12
assert abs(float(unrealized)) < 1e-12

print("PASS: DB stale open position reconciled to zero with no new trade row")
print("ALL_PHASE2G_STALE_POSITION_RECONCILER_TESTS_PASSED")
