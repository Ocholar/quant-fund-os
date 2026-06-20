from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8-sig")

marker = "QFOS_CANONICAL_BOUNDARY_HARDENING_V1"
if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

if "QFOS_CANONICAL_TRADE_LIFECYCLE_V1" not in src:
    raise SystemExit("PATCH_FAILED: canonical lifecycle block not found")

# ------------------------------------------------------------
# 1) Stable SELL idempotency key:
# Same open BUY lot + same sell quantity = same durable key.
# Exit source/reason deliberately excluded so competing paths cannot
# produce two SELL rows for one lot.
# ------------------------------------------------------------
old_key = '''        lifecycle_key = (
            f"SELL|{symbol}|lot={latest_open_buy_id}|"
            f"qty={round(qty, 10)}|"
            f"reason={normalized.get('exit_reason') or strategy}|"
            f"source={source}"
        )
'''

new_key = '''        lifecycle_key = (
            f"SELL|{symbol}|lot={latest_open_buy_id}|"
            f"qty={round(qty, 10)}"
        )
'''

if old_key not in src:
    raise SystemExit("PATCH_FAILED: current SELL lifecycle key block not found")

src = src.replace(old_key, new_key, 1)

# ------------------------------------------------------------
# 2) Add current-transaction runtime cache refresher.
# It reads uncommitted trigger results through the same caller-owned
# transaction, avoiding a second connection that cannot see the fill yet.
# ------------------------------------------------------------
anchor = 'def qfos_apply_fill_atomic(conn, fill, source="canonical"):'
if anchor not in src:
    raise SystemExit("PATCH_FAILED: canonical wrapper anchor not found")

helper = r'''
# ============================================================
# QFOS_CANONICAL_BOUNDARY_HARDENING_V1
# ============================================================

def _qfos_refresh_runtime_cache_from_active_conn(conn, source="canonical_active_conn"):
    """
    Refresh runtime portfolio cache using the same active DB transaction
    that just persisted the fill and fired position triggers.
    """
    try:
        rows = conn.execute(text("""
            SELECT symbol, quantity, avg_entry
            FROM positions
            WHERE quantity > 0.00000001
            ORDER BY symbol
        """)).mappings().all()

        ledger = conn.execute(text("""
            SELECT *
            FROM qfos_current_ledger_accounting()
            LIMIT 1
        """)).mappings().first()

        new_positions = {}
        new_entries = {}

        for row in rows:
            symbol = str(row.get("symbol") or "")
            qty = float(row.get("quantity") or 0.0)
            avg = float(row.get("avg_entry") or 0.0)

            if symbol and qty > 0.00000001:
                new_positions[symbol] = qty
                if avg > 0:
                    new_entries[symbol] = avg

        portfolio.positions.clear()
        portfolio.positions.update(new_positions)

        entry_prices.clear()
        entry_prices.update(new_entries)

        if ledger:
            cash = float(ledger.get("cash") or ledger.get("available_cash") or 0.0)
            equity = float(ledger.get("equity") or 0.0)

            if cash >= 0:
                portfolio.cash = cash
            if equity > 0:
                portfolio.equity = equity
                portfolio.peak = max(
                    float(getattr(portfolio, "peak", 0.0) or 0.0),
                    equity,
                )

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=position_updated authority=db_trade_trigger "
            f"source={source} open_positions={len(new_positions)} "
            f"cash={float(getattr(portfolio, 'cash', 0.0) or 0.0):.8f} "
            f"equity={float(getattr(portfolio, 'equity', 0.0) or 0.0):.8f}",
            flush=True,
        )
        return True

    except Exception as exc:
        print(
            "[TRADE_BOUNDARY_REJECT] "
            f"symbol=UNKNOWN side=UNKNOWN "
            f"reason=active_transaction_cache_refresh_error:{exc}",
            flush=True,
        )
        return False

'''

src = src.replace(anchor, helper + "\n" + anchor, 1)

# ------------------------------------------------------------
# 3) Refresh runtime cache only after atomic persistence succeeds.
# ------------------------------------------------------------
old_after_success = '''    try:
        trade = conn.execute(text("""
            SELECT id, quantity, fill_price, pnl, is_exit, exit_reason
'''

new_after_success = '''    _qfos_refresh_runtime_cache_from_active_conn(
        conn,
        source=f"post_persist:{source}",
    )

    try:
        trade = conn.execute(text("""
            SELECT id, quantity, fill_price, pnl, is_exit, exit_reason
'''

if old_after_success not in src:
    raise SystemExit("PATCH_FAILED: post-persist probe anchor not found")

src = src.replace(old_after_success, new_after_success, 1)

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
