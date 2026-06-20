from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT5_EXEC_BRIDGE_AUDIT_V1" in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT5_EXEC_BRIDGE_AUDIT_V1
# Purpose:
#   Every allocator rescue order must either:
#   1) persist through qfos_persist_fill_atomic(), or
#   2) log a specific rejection reason.
# ============================================================

def qfos_exec_bridge_audit(stage, **kwargs):
    try:
        parts = []
        for k, v in kwargs.items():
            if isinstance(v, float):
                parts.append(f"{k}={v:.12g}")
            else:
                parts.append(f"{k}={v}")
        print(f"[EXEC_BRIDGE_AUDIT] stage={stage} " + " ".join(parts), flush=True)
    except Exception as e:
        print(f"[EXEC_BRIDGE_AUDIT] stage=audit_error error={e}", flush=True)


def qfos_exec_bridge_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def qfos_exec_bridge_get_mark_price(symbol, order=None):
    if isinstance(order, dict):
        for key in ("fill_price", "expected_price", "price", "mark_price", "last_price"):
            val = qfos_exec_bridge_float(order.get(key), 0.0)
            if val > 0:
                return val

        feature = order.get("feature")
        if isinstance(feature, dict):
            for key in ("price", "last_price", "mark_price"):
                val = qfos_exec_bridge_float(feature.get(key), 0.0)
                if val > 0:
                    return val

    try:
        fobj = globals().get("features")
        if fobj is not None:
            for attr in ("features", "data", "by_symbol", "store"):
                d = getattr(fobj, attr, None)
                if isinstance(d, dict):
                    row = d.get(symbol)
                    if isinstance(row, dict):
                        val = qfos_exec_bridge_float(row.get("price") or row.get("last_price") or row.get("mark_price"), 0.0)
                        if val > 0:
                            return val
    except Exception:
        pass

    try:
        m = globals().get("market")
        if isinstance(m, dict):
            row = m.get(symbol)
            if isinstance(row, dict):
                val = qfos_exec_bridge_float(row.get("price") or row.get("last_price") or row.get("mark_price"), 0.0)
                if val > 0:
                    return val
            else:
                val = qfos_exec_bridge_float(row, 0.0)
                if val > 0:
                    return val
    except Exception:
        pass

    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT last_price, avg_entry
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()
        if row:
            val = qfos_exec_bridge_float(row.get("last_price"), 0.0)
            if val > 0:
                return val
            val = qfos_exec_bridge_float(row.get("avg_entry"), 0.0)
            if val > 0:
                return val
    except Exception:
        pass

    return 0.0


def qfos_exec_bridge_normalize_order(order, index=0):
    if not isinstance(order, dict):
        qfos_exec_bridge_audit("normalize_order", index=index, decision="DROP", reason="order_not_dict")
        return None, "order_not_dict"

    symbol = str(order.get("symbol") or "").strip()
    if not symbol:
        qfos_exec_bridge_audit("normalize_order", index=index, decision="DROP", reason="missing_symbol")
        return None, "missing_symbol"

    side = str(order.get("side") or "buy").strip().lower()
    if side not in ("buy", "sell"):
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, index=index, decision="DROP", reason="missing_side")
        return None, "missing_side"

    strategy = str(order.get("strategy") or order.get("entry_strategy") or "unknown").strip()
    source = str(order.get("source") or order.get("feature_source") or "").strip()

    price = qfos_exec_bridge_get_mark_price(symbol, order)
    if price <= 0:
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, strategy=strategy, side=side, decision="DROP", reason="missing_price")
        return None, "missing_price"

    qty = qfos_exec_bridge_float(order.get("quantity") or order.get("qty"), 0.0)
    value = qfos_exec_bridge_float(
        order.get("value") or order.get("notional") or order.get("target_value") or order.get("usd_value"),
        0.0,
    )

    if qty <= 0 and value > 0 and price > 0:
        qty = value / price

    if qty <= 0 and strategy == "evo_allocator_rescue" and side == "buy":
        default_value = 1.25
        try:
            a = qfos_agent5_ledger_accounting_snapshot() if "qfos_agent5_ledger_accounting_snapshot" in globals() else {}
            equity_hint = qfos_exec_bridge_float(a.get("expected_equity"), 100.0) if isinstance(a, dict) else 100.0
            default_value = min(1.25, max(0.50, equity_hint * 0.0125))
        except Exception:
            default_value = 1.25
        qty = default_value / price
        value = default_value

    if qty <= 0:
        qfos_exec_bridge_audit("normalize_order", symbol=symbol, strategy=strategy, side=side, price=price, decision="DROP", reason="missing_quantity")
        return None, "missing_quantity"

    fill = dict(order)
    fill["symbol"] = symbol
    fill["side"] = side
    fill["quantity"] = float(qty)
    fill["expected_price"] = float(fill.get("expected_price") or price)
    fill["fill_price"] = float(fill.get("fill_price") or price)
    fill["strategy"] = strategy
    fill["confidence"] = qfos_exec_bridge_float(fill.get("confidence"), 1.0 if strategy == "evo_allocator_rescue" else 0.0)
    fill["slippage_bps"] = qfos_exec_bridge_float(fill.get("slippage_bps"), 0.0)
    fill["shadow_mode"] = bool(fill.get("shadow_mode", False))
    fill["live"] = bool(fill.get("live", False))
    if source:
        fill["source"] = source

    qfos_exec_bridge_audit("normalize_order", index=index, symbol=symbol, strategy=strategy, side=side, qty=float(qty), price=float(fill["fill_price"]), decision="ALLOW")
    qfos_exec_bridge_audit("proposed_fill_created", symbol=symbol, side=side, qty=float(qty), fill_price=float(fill["fill_price"]), strategy=strategy)
    return fill, "ok"


def qfos_exec_bridge_recent_duplicate_buy(symbol, qty, price, strategy, seconds=90):
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) AS n
                FROM trades
                WHERE symbol = :symbol
                  AND lower(side) = 'buy'
                  AND strategy = :strategy
                  AND ABS(quantity - :qty) <= 0.00000001
                  AND ABS(fill_price - :price) <= 0.00000001
                  AND created_at >= CURRENT_TIMESTAMP - (:seconds || ' seconds')::interval
            """), {
                "symbol": symbol,
                "strategy": strategy,
                "qty": float(qty),
                "price": float(price),
                "seconds": int(seconds),
            }).mappings().first()
        return int((row or {}).get("n") or 0) > 0
    except Exception:
        return False


def qfos_exec_bridge_validate_fill(fill):
    symbol = str(fill.get("symbol") or "")
    side = str(fill.get("side") or "").lower()
    strategy = str(fill.get("strategy") or "")
    qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
    price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

    if not symbol:
        return False, "missing_symbol"
    if side not in ("buy", "sell"):
        return False, "missing_side"
    if qty <= 0:
        return False, "invalid_quantity"
    if price <= 0:
        return False, "invalid_price"

    if side == "buy":
        if qfos_exec_bridge_recent_duplicate_buy(symbol, qty, price, strategy):
            return False, "duplicate_order_blocked"

        try:
            prices_obj = globals().get("prices", {}) or globals().get("market", {}) or {}
            equity_obj = float(getattr(portfolio, "equity", 100.0) or 100.0)
            ok, reason = can_buy(symbol, fill, prices_obj, equity_obj)
            if not ok:
                reason_s = str(reason or "risk_gate_blocked")
                if "cooldown" in reason_s:
                    return False, "cooldown_blocked"
                if "already_holding" in reason_s or "max_open_positions" in reason_s:
                    return False, "existing_position_limit_blocked"
                if "exposure" in reason_s:
                    return False, "exposure_limit_blocked"
                return False, reason_s
        except NameError:
            pass
        except Exception as e:
            return False, f"risk_gate_error:{e}"

    return True, "ok"


def qfos_exec_bridge_after_persist_probe(symbol):
    try:
        with engine.begin() as conn:
            t = conn.execute(text("""
                SELECT id, quantity, fill_price
                FROM trades
                WHERE symbol = :symbol
                ORDER BY id DESC
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

            p = conn.execute(text("""
                SELECT quantity
                FROM positions
                WHERE symbol = :symbol
                LIMIT 1
            """), {"symbol": symbol}).mappings().first()

        trade_id = (t or {}).get("id")
        position_qty = qfos_exec_bridge_float((p or {}).get("quantity"), 0.0)
        return trade_id, position_qty
    except Exception:
        return None, None


def qfos_exec_bridge_persist_fill(fill, source="exec_bridge"):
    symbol = str(fill.get("symbol") or "")
    side = str(fill.get("side") or "").lower()
    strategy = str(fill.get("strategy") or "")
    qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
    price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

    qfos_exec_bridge_audit("before_persist", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy)

    try:
        with engine.begin() as conn:
            try:
                result = qfos_persist_fill_atomic(conn, fill, source=source)
            except TypeError:
                result = qfos_persist_fill_atomic(conn, fill)

        if result is False or result is None:
            qfos_exec_bridge_audit("persist_failed", symbol=symbol, side=side, reason="atomic_returned_false_or_none")
            return False

        trade_id, position_qty = qfos_exec_bridge_after_persist_probe(symbol)
        qfos_exec_bridge_audit("after_persist", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, trade_id=trade_id, position_qty=position_qty)
        return True

    except Exception as e:
        qfos_exec_bridge_audit("persist_failed", symbol=symbol, side=side, reason=str(e))
        return False


def qfos_exec_bridge_process_orders(raw_orders, source="exec_bridge"):
    if raw_orders is None:
        raw_orders = []
    if not isinstance(raw_orders, list):
        try:
            raw_orders = list(raw_orders)
        except Exception:
            raw_orders = []

    symbols = []
    strategies = []
    for o in raw_orders:
        if isinstance(o, dict):
            symbols.append(str(o.get("symbol") or ""))
            strategies.append(str(o.get("strategy") or ""))

    qfos_exec_bridge_audit("raw_orders_received", count=len(raw_orders), symbols=symbols, strategies=strategies, source=source)

    proposed = []
    for i, order in enumerate(raw_orders):
        fill, reason = qfos_exec_bridge_normalize_order(order, index=i)
        if fill is not None:
            proposed.append(fill)

    qfos_exec_bridge_audit("proposed_fills_summary", count=len(proposed), symbols=[str(f.get("symbol") or "") for f in proposed], strategies=[str(f.get("strategy") or "") for f in proposed])

    applied = 0

    for fill in proposed:
        symbol = str(fill.get("symbol") or "")
        side = str(fill.get("side") or "").lower()
        strategy = str(fill.get("strategy") or "")
        qty = qfos_exec_bridge_float(fill.get("quantity"), 0.0)
        price = qfos_exec_bridge_float(fill.get("fill_price") or fill.get("expected_price"), 0.0)

        ok, reason = qfos_exec_bridge_validate_fill(fill)
        if not ok:
            qfos_exec_bridge_audit("final_validation", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, decision="REJECT", reason=reason)
            continue

        qfos_exec_bridge_audit("final_validation", symbol=symbol, side=side, qty=qty, fill_price=price, strategy=strategy, decision="ALLOW")

        if qfos_exec_bridge_persist_fill(fill, source=source):
            applied += 1

    qfos_exec_bridge_audit("final_applied_summary", applied=applied, proposed=len(proposed), raw=len(raw_orders))
    return applied


# ============================================================
# End QFOS_AGENT5_EXEC_BRIDGE_AUDIT_V1
# ============================================================
'''

insert_marker = "# ============================================\n# QFOS RESCUE HARDENING + DUPLICATE SELL GUARD"
if insert_marker in text:
    text = text.replace(insert_marker, helper + "\n\n" + insert_marker, 1)
else:
    marker2 = "if __name__ == '__main__':"
    if marker2 in text:
        text = text.replace(marker2, helper + "\n\n" + marker2, 1)
    else:
        text += "\n\n" + helper + "\n"

old = 'print(f"[ALLOCATOR_RESCUE] injected_orders count={len(orders)}", flush=True)'
new = old + '''
        # QFOS_AGENT5_EXEC_BRIDGE_RESCUE_HOOK_V1
        try:
            if orders:
                _qfos_exec_bridge_applied = qfos_exec_bridge_process_orders(orders, source="allocator_rescue_hook")
                print(f"[EXEC_BRIDGE_AUDIT] stage=rescue_hook_result applied={_qfos_exec_bridge_applied} raw_orders={len(orders)}", flush=True)
                if _qfos_exec_bridge_applied > 0:
                    orders = []
        except Exception as _qfos_exec_bridge_hook_error:
            print(f"[EXEC_BRIDGE_AUDIT] stage=rescue_hook_error reason={_qfos_exec_bridge_hook_error}", flush=True)
'''

if old in text:
    text = text.replace(old, new, 1)
else:
    print("WARN: allocator rescue injected_orders print marker not found")

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
