import sqlite3, os, json, time

before_id = int(os.environ["BEFORE_ID"])
conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
has_exit_cols = "is_exit" in cols and "exit_reason" in cols

def rows(q, args=()):
    try:
        return cur.execute(q, args).fetchall()
    except Exception as e:
        return [("ERR", str(e))]

def scalar(q, args=(), default=0):
    try:
        row = cur.execute(q, args).fetchone()
        return row[0] if row else default
    except Exception:
        return default

print("TRADES_SCHEMA_HAS_EXIT_COLUMNS:")
print(has_exit_cols)

print("\nNEW_TRADES_AFTER_BASELINE:")
if has_exit_cols:
    new_trades = rows("""
        SELECT id, symbol, side, quantity, fill_price, pnl, strategy,
               COALESCE(is_exit, 'MISSING') AS is_exit,
               COALESCE(exit_reason, 'MISSING') AS exit_reason,
               created_at
        FROM trades
        WHERE id > ?
        ORDER BY id ASC
    """, (before_id,))
else:
    new_trades = rows("""
        SELECT id, symbol, side, quantity, fill_price, pnl, strategy, created_at
        FROM trades
        WHERE id > ?
        ORDER BY id ASC
    """, (before_id,))
print(new_trades if new_trades else "NONE")

print("\nSELLS_WITH_BAD_EXIT_ACCOUNTING:")
if has_exit_cols:
    bad_exit = rows("""
        SELECT id, symbol, side, quantity, strategy, is_exit, exit_reason, created_at
        FROM trades
        WHERE id > ?
          AND side='sell'
          AND (
            COALESCE(is_exit, 0)=0
            OR exit_reason IS NULL
            OR TRIM(exit_reason)=''
          )
        ORDER BY id ASC
    """, (before_id,))
    print(bad_exit if bad_exit else "NONE")
else:
    bad_exit = [("FAIL_SCHEMA_MISSING_EXIT_COLUMNS",)]
    print(bad_exit)

print("\nSELL_ONLY_LIFECYCLE_CHECK:")
buys = scalar("SELECT COUNT(*) FROM trades WHERE id > ? AND side='buy'", (before_id,), 0)
sells = scalar("SELECT COUNT(*) FROM trades WHERE id > ? AND side='sell'", (before_id,), 0)
print("buys", buys, "sells", sells)

print("\nNEW_DUPLICATE_SELL_PATTERNS:")
dup_sells = rows("""
    SELECT symbol, side, quantity, strategy, COUNT(*) AS c
    FROM trades
    WHERE id > ?
      AND side='sell'
    GROUP BY symbol, side, quantity, strategy
    HAVING c > 1
    ORDER BY c DESC
""", (before_id,))
print(dup_sells if dup_sells else "NONE")

print("\nNEGATIVE_POSITIONS:")
neg = rows("""
    SELECT symbol, quantity
    FROM positions
    WHERE quantity < -0.00000001
""")
print(neg if neg else "NONE")

print("\nOPEN_POSITIONS:")
open_pos = rows("""
    SELECT symbol, quantity, exposure, unrealized_pnl, realized_pnl, strategy, updated_at
    FROM positions
    WHERE quantity > 0.00000001
    ORDER BY symbol
""")
print(open_pos if open_pos else "NONE")

print("\nSTALE_PAPER_SYNC_POSITIONS_WITHOUT_BUY_LIFECYCLE:")
stale = rows("""
    SELECT p.symbol, p.quantity, p.exposure, p.strategy, p.updated_at
    FROM positions p
    WHERE p.quantity > 0.00000001
      AND COALESCE((
        SELECT SUM(t.quantity)
        FROM trades t
        WHERE t.symbol = p.symbol
          AND t.side='buy'
      ), 0) <= 0.00000001
    ORDER BY p.symbol
""")
print(stale if stale else "NONE")

print("\nSUMMARY_JSON:")
summary = {
    "before_id": before_id,
    "new_trade_count": len(new_trades) if isinstance(new_trades, list) and not (new_trades and new_trades[0][0] == "ERR") else -1,
    "new_buy_count": buys,
    "new_sell_count": sells,
    "bad_exit_count": len(bad_exit) if isinstance(bad_exit, list) and bad_exit != [("FAIL_SCHEMA_MISSING_EXIT_COLUMNS",)] else -1,
    "duplicate_sell_pattern_count": len(dup_sells) if isinstance(dup_sells, list) and not (dup_sells and dup_sells[0][0] == "ERR") else -1,
    "negative_position_count": len(neg) if isinstance(neg, list) and not (neg and neg[0][0] == "ERR") else -1,
    "open_position_count": len(open_pos) if isinstance(open_pos, list) and not (open_pos and open_pos[0][0] == "ERR") else -1,
    "stale_no_buy_position_count": len(stale) if isinstance(stale, list) and not (stale and stale[0][0] == "ERR") else -1,
    "has_exit_cols": has_exit_cols,
    "ts": time.time()
}
print(json.dumps(summary, indent=2))
