from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8")

# ============================================================
# 1) Fix SQLAlchemy positional-list execute bug in atomic helpers
# ============================================================

old = '''def _qfos_get_position_row(conn, symbol):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    select_cols = ["symbol", qty_col]
    if avg_col:
        select_cols.append(avg_col)
    if realized_col:
        select_cols.append(realized_col)

    sql = f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=? LIMIT 1"
    row = conn.execute(sql, (symbol,)).fetchone()

    if not row:
        return {
            "symbol": symbol,
            "quantity": 0.0,
            "avg_entry": 0.0,
            "realized_pnl": 0.0,
        }

    def _get(idx, default=None):
        try:
            return row[idx]
        except Exception:
            return default

    return {
        "symbol": _get(0, symbol),
        "quantity": float(_get(1, 0.0) or 0.0),
        "avg_entry": float(_get(2, 0.0) or 0.0) if avg_col else 0.0,
        "realized_pnl": float(_get(3, 0.0) or 0.0) if realized_col else 0.0,
    }
'''

new = '''def _qfos_get_position_row(conn, symbol):
    cols = _qfos_table_columns(conn, "positions")
    if not cols:
        raise RuntimeError("positions table missing or unreadable")

    qty_col = _qfos_first_existing_column(cols, ["quantity", "qty", "amount"])
    avg_col = _qfos_first_existing_column(cols, ["avg_entry", "avg_price", "entry_price", "average_entry"])
    realized_col = _qfos_first_existing_column(cols, ["realized_pnl", "pnl_realized"])

    if not qty_col:
        raise RuntimeError("positions table has no quantity column")

    select_cols = ["symbol", qty_col]
    if avg_col:
        select_cols.append(avg_col)
    if realized_col:
        select_cols.append(realized_col)

    sql = f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=:symbol LIMIT 1"
    row = _qfos_exec(conn, sql, {"symbol": symbol}).fetchone()

    if not row:
        return {
            "symbol": symbol,
            "quantity": 0.0,
            "avg_entry": 0.0,
            "realized_pnl": 0.0,
        }

    def _get(idx, default=None):
        try:
            return row[idx]
        except Exception:
            return default

    return {
        "symbol": _get(0, symbol),
        "quantity": float(_get(1, 0.0) or 0.0),
        "avg_entry": float(_get(2, 0.0) or 0.0) if avg_col else 0.0,
        "realized_pnl": float(_get(3, 0.0) or 0.0) if realized_col else 0.0,
    }
'''

if old in s:
    s = s.replace(old, new, 1)
    print("PATCHED _qfos_get_position_row")
else:
    print("SKIP _qfos_get_position_row exact block not found; continuing")


old = '''    if exists:
        assignments = ", ".join([f"{k}=?" for k in values.keys()])
        params = list(values.values()) + [symbol]
        conn.execute(f"UPDATE positions SET {assignments} WHERE symbol=?", params)
    else:
        insert_cols = ["symbol"] + list(values.keys())
        placeholders = ", ".join(["?"] * len(insert_cols))
        params = [symbol] + list(values.values())
        conn.execute(f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({placeholders})", params)
'''

new = '''    if exists:
        assignments = ", ".join([f"{k}=:{k}" for k in values.keys()])
        params = dict(values)
        params["__symbol"] = symbol
        _qfos_exec(
            conn,
            f"UPDATE positions SET {assignments} WHERE symbol=:__symbol",
            params,
        )
    else:
        insert_cols = ["symbol"] + list(values.keys())
        placeholders = ", ".join([f":{k}" for k in insert_cols])
        params = {"symbol": symbol}
        params.update(values)
        _qfos_exec(
            conn,
            f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({placeholders})",
            params,
        )
'''

if old not in s:
    raise SystemExit("FAILED: _qfos_upsert_position_atomic positional-list block not found")
s = s.replace(old, new, 1)
print("PATCHED _qfos_upsert_position_atomic SQLAlchemy params")


old = '''    placeholders = ", ".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})"
    conn.execute(sql, insert_vals)
'''

new = '''    placeholders = ", ".join([f":p{i}" for i in range(len(insert_cols))])
    sql = f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})"
    params = {f"p{i}": insert_vals[i] for i in range(len(insert_cols))}
    _qfos_exec(conn, sql, params)
'''

if old in s:
    s = s.replace(old, new, 1)
    print("PATCHED _qfos_insert_trade_atomic SQLAlchemy params")
else:
    print("SKIP _qfos_insert_trade_atomic exact block not found; may already be patched")


# ============================================================
# 2) Add rollback for pre-persistence BUYs rejected by final firewall
# ============================================================

helper = r'''
# ============================================================
# QFOS_AGENT1_PREPERSIST_BUY_ROLLBACK_V1
# Purpose:
#   apply_buy() mutates in-memory paper cash/positions before the
#   final firewall/persistence stage. If the final firewall rejects
#   that BUY, the runtime can keep a ghost position with no BUY row.
#   This rollback removes only that unpersisted in-memory mutation.
#   It does not change thresholds, risk, fallback logic, dashboard,
#   or Agent 5's atomic persistence boundary.
# ============================================================

def qfos_rollback_unpersisted_buy(fill, source="final_firewall"):
    try:
        if not isinstance(fill, dict):
            return False
        side = str(fill.get("side", "")).lower()
        if side != "buy":
            return False
        if bool(fill.get("shadow_mode", False)):
            return False

        symbol = str(fill.get("symbol") or "")
        if not symbol:
            return False

        qty = float(fill.get("quantity") or fill.get("qty") or 0.0)
        price = float(fill.get("fill_price") or fill.get("expected_price") or fill.get("price") or 0.0)
        if qty <= 0 or price <= 0:
            return False

        fee = qty * price * FEE_RATE
        current_qty = float(portfolio.positions.get(symbol, 0.0) or 0.0)

        # Only reverse up to the quantity that this rejected BUY added.
        rollback_qty = min(qty, max(current_qty, 0.0))
        if rollback_qty <= 0:
            return False

        portfolio.cash += rollback_qty * price + fee
        new_qty = current_qty - rollback_qty

        if new_qty <= 1e-08:
            portfolio.positions[symbol] = 0.0
            entry_prices.pop(symbol, None)
            position_open_time.pop(symbol, None)
            position_peak_change.pop(symbol, None)
            trade_counts[symbol] = max(0, int(trade_counts.get(symbol, 0) or 0) - 1)
        else:
            portfolio.positions[symbol] = new_qty

        print(
            f"[QFOS_PREPERSIST_BUY_ROLLBACK] symbol={symbol} qty={rollback_qty:.12f} "
            f"price={price:.12f} source={source}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[QFOS_PREPERSIST_BUY_ROLLBACK_ERROR] error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT1_PREPERSIST_BUY_ROLLBACK_V1
# ============================================================
'''

if "def qfos_rollback_unpersisted_buy(" not in s:
    marker = "\ndef apply_sell(symbol, qty, price, reason):"
    if marker not in s:
        raise SystemExit("FAILED: apply_sell marker not found for rollback helper insert")
    s = s.replace(marker, helper + marker, 1)
    print("INSERTED qfos_rollback_unpersisted_buy")
else:
    print("qfos_rollback_unpersisted_buy already present")


old = '''                        rejected.append({'symbol': fill.get('symbol', 'UNKNOWN'), 'reason': reason})
'''

new = '''                        if str(fill.get('side', '')).lower() == 'buy':
                            qfos_rollback_unpersisted_buy(fill, source=f"final_firewall:{reason}")
                        rejected.append({'symbol': fill.get('symbol', 'UNKNOWN'), 'reason': reason})
'''

count = s.count(old)
if count < 1:
    raise SystemExit("FAILED: final_firewall rejected.append block not found")
s = s.replace(old, new, 1)
print("PATCHED final firewall rejected BUY rollback")


# ============================================================
# 3) Clean active runtime dicts on startup when DB is clean
# ============================================================

startup_guard = r'''
# ============================================================
# QFOS_AGENT1_CLEAN_BASELINE_RUNTIME_GUARD_V1
# Purpose:
#   If the DB is at a clean paper baseline, force in-memory runtime
#   position containers to agree. This prevents stale process-local
#   state from being synced back into DB as paper_position_sync.
# ============================================================

def qfos_clean_runtime_state_if_db_baseline():
    try:
        with engine.begin() as conn:
            trades_n = conn.execute(text("SELECT COUNT(*) FROM trades")).scalar()
            open_n = conn.execute(text("SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001")).scalar()
            snap = conn.execute(text("""
                SELECT equity, cash, exposure
                FROM portfolio_snapshots
                ORDER BY id DESC
                LIMIT 1
            """)).mappings().first()

        equity = float((snap or {}).get("equity") or INITIAL_EQUITY)
        cash = float((snap or {}).get("cash") or INITIAL_EQUITY)
        exposure = float((snap or {}).get("exposure") or 0.0)

        if int(trades_n or 0) == 0 and int(open_n or 0) == 0 and abs(equity - 100.0) < 1e-06 and abs(cash - 100.0) < 1e-06 and abs(exposure) < 1e-06:
            portfolio.cash = 100.0
            portfolio.positions.clear()
            entry_prices.clear()
            position_open_time.clear()
            position_peak_change.clear()
            shadow_positions.clear()
            shadow_entry_prices.clear()
            shadow_trade_counts.clear()
            print("[QFOS_CLEAN_BASELINE_RUNTIME_GUARD] cleared in-memory positions for clean paper baseline", flush=True)
            return True
    except Exception as e:
        print(f"[QFOS_CLEAN_BASELINE_RUNTIME_GUARD_ERROR] error={e}", flush=True)
    return False

# ============================================================
# End QFOS_AGENT1_CLEAN_BASELINE_RUNTIME_GUARD_V1
# ============================================================
'''

if "def qfos_clean_runtime_state_if_db_baseline(" not in s:
    marker = "print('Quant Fund OS starting. LIVE_TRADING=', settings.live_trading)"
    if marker not in s:
        raise SystemExit("FAILED: startup print marker not found")
    s = s.replace(marker, startup_guard + "\nqfos_clean_runtime_state_if_db_baseline()\n" + marker, 1)
    print("INSERTED clean baseline runtime guard")
else:
    print("clean baseline runtime guard already present")


p.write_text(s, encoding="utf-8")
print("AGENT1_PHASE3A_PATCH_WRITE_OK")
