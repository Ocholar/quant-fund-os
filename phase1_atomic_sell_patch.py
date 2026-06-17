from pathlib import Path
import re
from datetime import datetime

p = Path("main.py")
if not p.exists():
    raise SystemExit("main.py not found. Run from C:\\Users\\Administrator\\Documents\\quant-fund-os")

s = p.read_text(encoding="utf-8")

# Remove old copy of this exact boundary if already present.
start_marker = "# ============================================================\n# QFOS_ATOMIC_FILL_PERSISTENCE_V1"
end_marker = "# ============================================================\n# END QFOS_ATOMIC_FILL_PERSISTENCE_V1\n# ============================================================"
if start_marker in s and end_marker in s:
    start = s.find(start_marker)
    end = s.find(end_marker, start) + len(end_marker)
    s = s[:start].rstrip() + "\n\n" + s[end:].lstrip()
    print("REMOVED_EXISTING_ATOMIC_BOUNDARY")

def replace_func(src, name, new_body):
    pat = re.compile(
        rf"^def {re.escape(name)}\([^\n]*\):\n.*?(?=^def |^class |^if __name__|\Z)",
        re.M | re.S,
    )
    m = pat.search(src)
    if not m:
        raise SystemExit(f"FUNCTION_NOT_FOUND: {name}")
    return src[:m.start()] + new_body.rstrip() + "\n\n" + src[m.end():]

# Route legacy save_trade helper through the atomic boundary.
s = replace_func(s, "save_trade", """def save_trade(conn, fill):
    # Preserve legacy helper name, but route through the single atomic boundary.
    return qfos_persist_fill_atomic(conn, fill, source='save_trade')
""")

atomic = r'''

# ============================================================
# QFOS_ATOMIC_FILL_PERSISTENCE_V1
# Single validation/persistence boundary for paper BUY/SELL rows.
# Prevents duplicate/oversized SELL rows from Profit Engine,
# watchdogs, or the main loop when DB open quantity is already zero.
# ============================================================

def _qfos_atomic_get(fill, key, default=None):
    try:
        if isinstance(fill, dict):
            return fill.get(key, default)
        return getattr(fill, key, default)
    except Exception:
        return default


def _qfos_atomic_float(value, default=0.0):
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _qfos_atomic_is_sqlalchemy(conn):
    try:
        return conn.__class__.__module__.startswith("sqlalchemy")
    except Exception:
        return False


def _qfos_atomic_select_position(conn, symbol):
    if _qfos_atomic_is_sqlalchemy(conn):
        return conn.execute(text("""
            SELECT symbol, quantity, avg_entry, realized_pnl, strategy
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

    return conn.execute("""
        SELECT symbol, quantity, avg_entry, realized_pnl, strategy
        FROM positions
        WHERE symbol = ?
        LIMIT 1
    """, (symbol,)).fetchone()


def _qfos_atomic_row_get(row, key, index, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return default


def _qfos_atomic_upsert_position(conn, payload):
    if _qfos_atomic_is_sqlalchemy(conn):
        conn.execute(text("""
            INSERT INTO positions(
                symbol, quantity, avg_entry, realized_pnl,
                unrealized_pnl, last_price, exposure, strategy, updated_at
            )
            VALUES(
                :symbol, :quantity, :avg_entry, :realized_pnl,
                :unrealized_pnl, :last_price, :exposure, :strategy,
                DATETIME('now', '+3 hours')
            )
            ON CONFLICT (symbol)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                avg_entry = EXCLUDED.avg_entry,
                realized_pnl = EXCLUDED.realized_pnl,
                unrealized_pnl = EXCLUDED.unrealized_pnl,
                last_price = EXCLUDED.last_price,
                exposure = EXCLUDED.exposure,
                strategy = EXCLUDED.strategy,
                updated_at = DATETIME('now', '+3 hours')
        """), payload)
        return

    conn.execute("""
        INSERT INTO positions(
            symbol, quantity, avg_entry, realized_pnl,
            unrealized_pnl, last_price, exposure, strategy, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, DATETIME('now', '+3 hours'))
        ON CONFLICT(symbol) DO UPDATE SET
            quantity = excluded.quantity,
            avg_entry = excluded.avg_entry,
            realized_pnl = excluded.realized_pnl,
            unrealized_pnl = excluded.unrealized_pnl,
            last_price = excluded.last_price,
            exposure = excluded.exposure,
            strategy = excluded.strategy,
            updated_at = DATETIME('now', '+3 hours')
    """, (
        payload["symbol"],
        payload["quantity"],
        payload["avg_entry"],
        payload["realized_pnl"],
        payload["unrealized_pnl"],
        payload["last_price"],
        payload["exposure"],
        payload["strategy"],
    ))


def _qfos_atomic_insert_trade(conn, payload):
    created_at = payload.get("created_at")

    if _qfos_atomic_is_sqlalchemy(conn):
        if created_at:
            conn.execute(text("""
                INSERT INTO trades(
                    symbol, side, quantity, expected_price, fill_price,
                    slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
                )
                VALUES(
                    :symbol, :side, :quantity, :expected_price, :fill_price,
                    :slippage_bps, :pnl, :strategy, :confidence, :live, :shadow_mode, :created_at
                )
            """), payload)
        else:
            conn.execute(text("""
                INSERT INTO trades(
                    symbol, side, quantity, expected_price, fill_price,
                    slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
                )
                VALUES(
                    :symbol, :side, :quantity, :expected_price, :fill_price,
                    :slippage_bps, :pnl, :strategy, :confidence, :live, :shadow_mode,
                    DATETIME('now', '+3 hours')
                )
            """), payload)
        return

    conn.execute("""
        INSERT INTO trades(
            symbol, side, quantity, expected_price, fill_price,
            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, DATETIME('now', '+3 hours')))
    """, (
        payload["symbol"],
        payload["side"],
        payload["quantity"],
        payload["expected_price"],
        payload["fill_price"],
        payload["slippage_bps"],
        payload["pnl"],
        payload["strategy"],
        payload["confidence"],
        int(bool(payload["live"])),
        int(bool(payload["shadow_mode"])),
        created_at,
    ))


def qfos_persist_fill_atomic(conn, fill, source="main_loop"):
    """
    One atomic paper persistence boundary.

    SELL invariant:
    - reads DB open quantity inside caller-owned transaction
    - open_qty <= 0 => reject, no trade row
    - requested_qty <= 0 => reject, no trade row
    - requested_qty > open_qty => cap to open_qty, never negative
    - update positions first
    - insert exactly one trade row after validation passes
    """
    try:
        side = str(_qfos_atomic_get(fill, "side", "") or "").strip().lower()
        symbol = str(_qfos_atomic_get(fill, "symbol", "") or "").strip()
        strategy = str(_qfos_atomic_get(fill, "strategy", "unknown") or "unknown")

        requested_qty = abs(_qfos_atomic_float(
            _qfos_atomic_get(fill, "quantity", _qfos_atomic_get(fill, "qty", 0.0)),
            0.0,
        ))

        price = _qfos_atomic_float(
            _qfos_atomic_get(fill, "fill_price", None)
            or _qfos_atomic_get(fill, "expected_price", None)
            or _qfos_atomic_get(fill, "price", None),
            0.0,
        )

        expected_price = _qfos_atomic_float(_qfos_atomic_get(fill, "expected_price", price), price)
        slippage_bps = _qfos_atomic_float(_qfos_atomic_get(fill, "slippage_bps", 0.0), 0.0)
        confidence = _qfos_atomic_float(_qfos_atomic_get(fill, "confidence", 1.0), 1.0)
        live = bool(_qfos_atomic_get(fill, "live", getattr(settings, "live_trading", False)))
        shadow_mode = bool(_qfos_atomic_get(fill, "shadow_mode", False))
        created_at = _qfos_atomic_get(fill, "created_at", None)

        if not symbol or side not in ("buy", "sell"):
            print(
                f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol or 'UNKNOWN'} "
                f"side={side or 'UNKNOWN'} reason=invalid_symbol_or_side",
                flush=True,
            )
            return None

        if requested_qty <= 0:
            print(
                f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol} side={side} "
                f"reason=requested_qty_lte_zero qty={requested_qty}",
                flush=True,
            )
            return None

        if price <= 0:
            print(
                f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol} side={side} "
                f"reason=price_lte_zero price={price}",
                flush=True,
            )
            return None

        existing = _qfos_atomic_select_position(conn, symbol)
        existing_qty = _qfos_atomic_float(_qfos_atomic_row_get(existing, "quantity", 1, 0.0), 0.0)
        avg_entry = _qfos_atomic_float(_qfos_atomic_row_get(existing, "avg_entry", 2, 0.0), 0.0)
        realized_pnl = _qfos_atomic_float(_qfos_atomic_row_get(existing, "realized_pnl", 3, 0.0), 0.0)
        existing_strategy = str(_qfos_atomic_row_get(existing, "strategy", 4, "unknown") or "unknown")

        final_qty = requested_qty
        fill_pnl = 0.0
        applied_strategy = strategy

        if side == "buy":
            fee_adjusted_price = price * (1 + FEE_RATE)
            new_qty = existing_qty + final_qty
            new_avg_entry = (
                ((existing_qty * avg_entry) + (final_qty * fee_adjusted_price)) / new_qty
                if new_qty > 0 else 0.0
            )
            new_realized_pnl = realized_pnl
            new_strategy = strategy or existing_strategy
            applied_strategy = new_strategy

        else:
            if existing_qty <= 1e-12:
                print(
                    f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol} strategy={strategy} "
                    f"reason=open_qty_lte_zero open_qty={existing_qty:.12f} "
                    f"requested_qty={requested_qty:.12f}",
                    flush=True,
                )
                return None

            if final_qty > existing_qty:
                print(
                    f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol} strategy={strategy} "
                    f"reason=qty_gt_open_capped requested_qty={final_qty:.12f} "
                    f"open_qty={existing_qty:.12f}",
                    flush=True,
                )
                final_qty = existing_qty

            if final_qty <= 1e-12:
                print(
                    f"[SELL_VALIDATION_REJECT] source={source} symbol={symbol} strategy={strategy} "
                    f"reason=final_qty_lte_zero final_qty={final_qty:.12f}",
                    flush=True,
                )
                return None

            net_sell_price = price * (1 - FEE_RATE)
            fill_pnl = final_qty * (net_sell_price - avg_entry)
            new_qty = max(existing_qty - final_qty, 0.0)

            if new_qty <= 1e-12:
                new_qty = 0.0

            new_avg_entry = avg_entry if new_qty > 0 else 0.0
            new_realized_pnl = realized_pnl + fill_pnl
            new_strategy = existing_strategy if new_qty > 0 else strategy
            applied_strategy = existing_strategy

        exposure = new_qty * price
        unrealized_pnl = new_qty * (price - new_avg_entry) if new_qty > 0 else 0.0

        _qfos_atomic_upsert_position(conn, {
            "symbol": symbol,
            "quantity": new_qty,
            "avg_entry": new_avg_entry,
            "realized_pnl": new_realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "last_price": price,
            "exposure": exposure,
            "strategy": new_strategy,
        })

        normalized = dict(fill) if isinstance(fill, dict) else {}
        normalized.update({
            "symbol": symbol,
            "side": side,
            "quantity": final_qty,
            "expected_price": expected_price,
            "fill_price": price,
            "slippage_bps": slippage_bps,
            "pnl": fill_pnl,
            "strategy": strategy,
            "confidence": confidence,
            "live": live,
            "shadow_mode": shadow_mode,
            "created_at": created_at,
            "applied_strategy": applied_strategy,
            "position_qty_after": new_qty,
        })

        _qfos_atomic_insert_trade(conn, normalized)
        return normalized

    except Exception as exc:
        print(
            f"[SELL_VALIDATION_REJECT] source={source} "
            f"symbol={_qfos_atomic_get(fill, 'symbol', 'UNKNOWN')} "
            f"reason=atomic_error error={exc}",
            flush=True,
        )
        return None

# ============================================================
# END QFOS_ATOMIC_FILL_PERSISTENCE_V1
# ============================================================
'''

marker = "    return (fill_pnl, applied_strategy)\n"
if marker not in s:
    raise SystemExit("ATOMIC_INSERT_MARKER_NOT_FOUND: return (fill_pnl, applied_strategy)")
s = s.replace(marker, marker + atomic, 1)

old_main_block = '''                for fill in applied_fills:
                    fill_pnl, original_strat = update_position_from_fill(conn, fill)
                    fill['pnl'] = fill_pnl
                    trades_total.inc()
                    conn.execute(text("\\n                        INSERT INTO trades(\\n                            symbol, side, quantity, expected_price, fill_price,\\n            slippage_bps, pnl, strategy, confidence, live, shadow_mode, created_at\\n                        )\\n                        VALUES(\\n                            :symbol, :side, :quantity, :expected_price, :fill_price,\\n                            :slippage_bps, :pnl, :strategy, :confidence, :live, :shadow_mode, DATETIME('now', '+3 hours')\\n                        )\\n                    "), fill | {'live': settings.live_trading, 'shadow_mode': fill.get('shadow_mode', False)})
                    side = fill.get('side', '').upper()
                    symbol = fill.get('symbol', '')
                    qty = float(fill.get('quantity', 0))
                    price = float(fill.get('fill_price', 0))
                    strategy = fill.get('strategy', 'unknown')
                    confidence = float(fill.get('confidence', 0))
                    is_shadow = fill.get('shadow_mode', False)
                    if not is_shadow:
                        pos_row = conn.execute(text('SELECT quantity FROM positions WHERE symbol = :s'), {'s': symbol}).mappings().first()
                        if pos_row:
                            portfolio.positions[symbol] = float(pos_row['quantity'])
                        else:
                            portfolio.positions[symbol] = 0.0

                    print(
                        f"[EXECUTION_STAGE] db_trade_written side={side} symbol={symbol} "
                        f"qty={qty:.8f} price={price:.8f} pnl={fill_pnl:.6f} "
                        f"position_qty={portfolio.positions.get(symbol, 0.0)}",
                        flush=True,
                    )
                    send_telegram_alert(f"<b>{side} {('(SHADOW)' if is_shadow else '')}</b> {symbol}\\nQty: {qty:.6f}\\nPrice: {price:.4f}\\nPnL: {fill_pnl:.2f}\\nStrategy: {strategy}\\nConfidence: {confidence:.2f}\\nLive: {settings.live_trading}")
                    score_strategy = original_strat if side == 'SELL' else strategy
'''

new_main_block = '''                for raw_fill in applied_fills:
                    persisted_fill = qfos_persist_fill_atomic(conn, raw_fill, source='main_loop')
                    if not persisted_fill:
                        continue
                    fill = persisted_fill
                    fill_pnl = float(fill.get('pnl', 0.0) or 0.0)
                    original_strat = fill.get('applied_strategy', fill.get('strategy', 'unknown'))
                    trades_total.inc()
                    side = fill.get('side', '').upper()
                    symbol = fill.get('symbol', '')
                    qty = float(fill.get('quantity', 0) or 0)
                    price = float(fill.get('fill_price', 0) or 0)
                    strategy = fill.get('strategy', 'unknown')
                    confidence = float(fill.get('confidence', 0) or 0)
                    is_shadow = fill.get('shadow_mode', False)
                    if not is_shadow:
                        pos_row = conn.execute(text('SELECT quantity FROM positions WHERE symbol = :s'), {'s': symbol}).mappings().first()
                        if pos_row:
                            portfolio.positions[symbol] = float(pos_row['quantity'])
                        else:
                            portfolio.positions[symbol] = 0.0

                    print(
                        f"[EXECUTION_STAGE] db_trade_written side={side} symbol={symbol} "
                        f"qty={qty:.8f} price={price:.8f} pnl={fill_pnl:.6f} "
                        f"position_qty={portfolio.positions.get(symbol, 0.0)}",
                        flush=True,
                    )
                    send_telegram_alert(f"<b>{side} {('(SHADOW)' if is_shadow else '')}</b> {symbol}\\nQty: {qty:.6f}\\nPrice: {price:.4f}\\nPnL: {fill_pnl:.2f}\\nStrategy: {strategy}\\nConfidence: {confidence:.2f}\\nLive: {settings.live_trading}")
                    score_strategy = original_strat if side == 'SELL' else strategy
'''

if old_main_block in s:
    s = s.replace(old_main_block, new_main_block, 1)
    print("PATCHED_MAIN_APPLIED_FILLS_PERSISTENCE")
elif "qfos_persist_fill_atomic(conn, raw_fill, source='main_loop')" in s:
    print("MAIN_APPLIED_FILLS_PERSISTENCE_ALREADY_PATCHED")
else:
    raise SystemExit("MAIN_APPLIED_FILLS_PERSISTENCE_BLOCK_NOT_FOUND")

watchdog_body = '''def _qfos_watchdog_close_worst_loser_once():
    import sqlite3
    from datetime import timedelta

    db = _qfos_watchdog_db_path()
    conn = sqlite3.connect(db, timeout=10)
    cur = conn.cursor()

    try:
        _qfos_watchdog_ensure_tables(cur)

        equity, regime = _qfos_watchdog_latest_equity_and_regime(cur)
        basket_cap = max(equity, 1.0) * _qfos_watchdog_basket_cap_pct(regime)
        min_position_loss = max(equity, 1.0) * QFOS_WATCHDOG_MIN_POSITION_LOSS_PCT

        rows = cur.execute("""
            SELECT symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, strategy
            FROM positions
            WHERE quantity > 0
        """).fetchall()

        if not rows:
            conn.close()
            return

        losing = []
        for symbol, quantity, avg_entry, last_price, exposure, unrealized_pnl, strategy in rows:
            qty = abs(_qfos_watchdog_float(quantity))
            entry = _qfos_watchdog_float(avg_entry)
            mark = _qfos_watchdog_float(last_price)
            db_unreal = _qfos_watchdog_float(unrealized_pnl)

            if qty <= 0 or entry <= 0 or mark <= 0:
                continue

            calc_loss = max((entry - mark) * qty, 0.0)
            db_loss = max(-db_unreal, 0.0)
            loss = max(calc_loss, db_loss)

            if loss > 0:
                losing.append({
                    "symbol": symbol,
                    "quantity": qty,
                    "avg_entry": entry,
                    "last_price": mark,
                    "exposure": _qfos_watchdog_float(exposure),
                    "unrealized_pnl": db_unreal,
                    "strategy": strategy,
                    "loss": loss,
                })

        basket_loss = sum(x["loss"] for x in losing)

        if basket_loss < basket_cap:
            conn.close()
            return

        losing = [x for x in losing if x["loss"] >= min_position_loss]
        if not losing:
            conn.close()
            return

        worst = sorted(losing, key=lambda x: x["loss"], reverse=True)[0]

        symbol = str(worst["symbol"])
        strategy = str(worst["strategy"] or "unknown_strategy")
        qty = worst["quantity"]
        mark = worst["last_price"]
        loss = worst["loss"]
        pnl = -abs(loss)

        now = _qfos_watchdog_now_local()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")
        blocked_until = (now + timedelta(hours=float(QFOS_WATCHDOG_SYMBOL_COOLDOWN_HOURS))).strftime("%Y-%m-%d %H:%M:%S")

        print(
            "[EMERGENCY_BASKET_WATCHDOG] closing worst loser "
            f"symbol={symbol} strategy={strategy} qty={qty:.8f} mark={mark:.8f} "
            f"position_loss={loss:.6f} basket_loss={basket_loss:.6f} "
            f"basket_cap={basket_cap:.6f} equity={equity:.2f} regime={regime}",
            flush=True,
        )

        persisted = qfos_persist_fill_atomic(cur, {
            "symbol": symbol,
            "side": "sell",
            "quantity": qty,
            "expected_price": mark,
            "fill_price": mark,
            "slippage_bps": 0.0,
            "strategy": "basket_loss_cap",
            "confidence": 1.0,
            "live": False,
            "shadow_mode": False,
            "created_at": now_s,
        }, source="emergency_basket_watchdog")

        if not persisted:
            conn.rollback()
            return

        pnl = float(persisted.get("pnl", pnl) or 0.0)

        cur.execute("""
            INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at)
            VALUES (?, 'basket_loss_cap', ?, ?)
        """, (symbol, blocked_until, now_s))

        if strategy and strategy != "unknown_strategy":
            cur.execute("""
                INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at)
                VALUES (?, 'basket_loss_cap', ?, ?)
            """, (strategy, blocked_until, now_s))

        conn.commit()

        print(
            "[EMERGENCY_BASKET_WATCHDOG] closed "
            f"symbol={symbol} pnl={pnl:.6f} blocked_until={blocked_until}",
            flush=True,
        )

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[EMERGENCY_BASKET_WATCHDOG] error={exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass
'''

poswd_body = '''def _qfos_poswd_close_position(cur, pos, reason, pnl, now_s):
    from datetime import timedelta

    symbol = str(pos["symbol"])
    qty = abs(_qfos_poswd_float(pos["quantity"]))
    mark = _qfos_poswd_float(pos["last_price"])
    strategy = str(pos.get("strategy") or "unknown_strategy")

    blocked_until = (_qfos_poswd_now_local() + timedelta(hours=float(QFOS_POSITION_WATCHDOG_COOLDOWN_HOURS))).strftime("%Y-%m-%d %H:%M:%S")

    print(
        "[ACTIVE_POSITION_WATCHDOG] closing "
        f"symbol={symbol} reason={reason} qty={qty:.8f} mark={mark:.8f} "
        f"pnl={pnl:.6f}",
        flush=True,
    )

    persisted = qfos_persist_fill_atomic(cur, {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": mark,
        "fill_price": mark,
        "slippage_bps": 0.0,
        "strategy": reason,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "created_at": now_s,
    }, source="active_position_watchdog")

    if not persisted:
        return False

    pnl = float(persisted.get("pnl", pnl) or 0.0)

    cur.execute("""
        INSERT OR REPLACE INTO symbol_quarantine(symbol, reason, blocked_until, created_at)
        VALUES (?, ?, ?, ?)
    """, (symbol, reason, blocked_until, now_s))

    if strategy and strategy != "unknown_strategy":
        cur.execute("""
            INSERT OR REPLACE INTO strategy_quarantine(strategy, reason, blocked_until, created_at)
            VALUES (?, ?, ?, ?)
        """, (strategy, reason, blocked_until, now_s))

    cur.execute("""
        INSERT OR REPLACE INTO position_peak_state(symbol, peak_unrealized_pnl, first_seen_at, last_seen_at, last_reason)
        VALUES (?, 0.0, ?, ?, ?)
    """, (symbol, now_s, now_s, reason))

    print(
        "[ACTIVE_POSITION_WATCHDOG] closed "
        f"symbol={symbol} reason={reason} pnl={pnl:.6f} blocked_until={blocked_until}",
        flush=True,
    )
    return True
'''

pe_body = '''def _qfos_pe_sell(cur, pos, quantity, reason, pnl, now_s, quarantine=True):
    symbol = str(pos["symbol"])
    qty = abs(_qfos_pe_float(quantity))
    mark = _qfos_pe_float(pos["last_price"])
    strategy = str(pos.get("strategy") or reason)

    if qty <= 0:
        print(
            f"[SELL_VALIDATION_REJECT] source=profit_engine symbol={symbol} "
            f"strategy={reason} reason=requested_qty_lte_zero qty={qty}",
            flush=True,
        )
        return False

    print(
        "[PROFIT_ENGINE] selling "
        f"symbol={symbol} reason={reason} qty={qty:.8f} mark={mark:.8f} pnl={pnl:.6f}",
        flush=True,
    )

    persisted = qfos_persist_fill_atomic(cur, {
        "symbol": symbol,
        "side": "sell",
        "quantity": qty,
        "expected_price": mark,
        "fill_price": mark,
        "slippage_bps": 0.0,
        "strategy": reason,
        "confidence": 1.0,
        "live": False,
        "shadow_mode": False,
        "created_at": now_s,
    }, source="profit_engine")

    if not persisted:
        return False

    final_qty = abs(_qfos_pe_float(persisted.get("quantity", qty)))
    final_pnl = _qfos_pe_float(persisted.get("pnl", pnl))

    row = cur.execute("SELECT quantity FROM positions WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
    remaining_qty = _qfos_pe_float(row[0], 0.0) if row else 0.0

    if remaining_qty <= 1e-12:
        if quarantine:
            blocked_until = _qfos_pe_quarantine(cur, symbol, strategy, reason, now_s)
            print(
                f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason} "
                f"pnl={final_pnl:.6f} blocked_until={blocked_until}",
                flush=True,
            )
        else:
            print(
                f"[PROFIT_ENGINE] closed symbol={symbol} reason={reason} "
                f"pnl={final_pnl:.6f}",
                flush=True,
            )
    else:
        print(
            f"[PROFIT_ENGINE] partial sold symbol={symbol} sold_qty={final_qty:.8f} "
            f"remaining_qty={remaining_qty:.8f} pnl={final_pnl:.6f}",
            flush=True,
        )

    return True
'''

s = replace_func(s, "_qfos_watchdog_close_worst_loser_once", watchdog_body)
s = replace_func(s, "_qfos_poswd_close_position", poswd_body)
s = replace_func(s, "_qfos_pe_sell", pe_body)

p.write_text(s, encoding="utf-8")
print("PATCH_WRITE_OK")
