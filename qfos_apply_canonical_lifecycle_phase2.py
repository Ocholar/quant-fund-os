from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8-sig")

marker = "QFOS_CANONICAL_TRADE_LIFECYCLE_V1"
if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

anchor = "if __name__ == '__main__':"
idx = src.rfind(anchor)

if idx < 0:
    anchor = 'if __name__ == "__main__":'
    idx = src.rfind(anchor)

if idx < 0:
    raise SystemExit("PATCH_FAILED: runtime entrypoint anchor not found")

block = r'''
# ============================================================
# QFOS_CANONICAL_TRADE_LIFECYCLE_V1
#
# Canonical authority:
#   trade intent -> atomic core -> DB trade trigger -> DB positions
#   -> DB ledger snapshot -> runtime cache refresh
#
# Legacy apply_buy/apply_sell previously mutated the real in-memory
# portfolio before persistence. They are replaced below with intent-only
# builders. The existing DB atomic core remains the only trade writer.
# ============================================================

_QFOS_ATOMIC_FILL_CORE_V1 = qfos_persist_fill_atomic

def qfos_refresh_runtime_portfolio_from_db(source="canonical_refresh"):
    """
    Refresh runtime cache from PostgreSQL only.
    Never write DB positions from portfolio memory.
    """
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT symbol, quantity, avg_entry, last_price
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
                portfolio.peak = max(float(getattr(portfolio, "peak", 0.0) or 0.0), equity)

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=runtime_cache_refreshed source={source} "
            f"open_positions={len(new_positions)} "
            f"cash={float(getattr(portfolio, 'cash', 0.0) or 0.0):.8f} "
            f"equity={float(getattr(portfolio, 'equity', 0.0) or 0.0):.8f}",
            flush=True,
        )
        return True

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=UNKNOWN "
            f"reason=runtime_cache_refresh_error:{e}",
            flush=True,
        )
        return False


def qfos_apply_fill_atomic(conn, fill, source="canonical"):
    """
    The sole canonical runtime boundary.

    Existing callers may still invoke qfos_persist_fill_atomic; the global
    alias below routes all of them into this wrapper and then into the
    preserved atomic core.
    """
    if not isinstance(fill, dict):
        print(
            "[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=UNKNOWN "
            "reason=fill_not_dict",
            flush=True,
        )
        return False

    normalized = dict(fill)

    symbol = str(normalized.get("symbol") or "").strip()
    side = str(normalized.get("side") or "").lower().strip()
    qty = float(normalized.get("quantity") or normalized.get("qty") or 0.0)
    price = float(
        normalized.get("fill_price")
        or normalized.get("expected_price")
        or normalized.get("price")
        or 0.0
    )
    strategy = str(normalized.get("strategy") or normalized.get("reason") or "unknown")
    exit_reason = str(normalized.get("exit_reason") or normalized.get("reason") or "")

    if side in ("long", "open"):
        side = "buy"
    elif side in ("close", "short"):
        side = "sell"

    if not symbol or side not in ("buy", "sell") or qty <= 0 or price <= 0:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side={side or 'UNKNOWN'} "
            "reason=invalid_symbol_side_qty_or_price",
            flush=True,
        )
        return False

    normalized["symbol"] = symbol
    normalized["side"] = side
    normalized["quantity"] = qty
    normalized["qty"] = qty
    normalized["fill_price"] = price
    normalized["expected_price"] = float(normalized.get("expected_price") or price)
    normalized["strategy"] = strategy
    normalized["source"] = str(source or normalized.get("source") or "canonical")

    if side == "sell":
        normalized["is_exit"] = True
        normalized["exit_reason"] = exit_reason or strategy
    else:
        normalized["is_exit"] = bool(normalized.get("is_exit", False))

    position_before = 0.0
    cost_basis_before = 0.0

    try:
        row = conn.execute(text("""
            SELECT quantity, avg_entry
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

        position_before = float((row or {}).get("quantity") or 0.0)
        cost_basis_before = float((row or {}).get("avg_entry") or 0.0)
    except Exception:
        pass

    lifecycle_key = (
        f"{symbol}|{side}|{round(qty, 10)}|"
        f"{normalized.get('exit_reason') or strategy}|{source}"
    )
    normalized["lifecycle_key"] = lifecycle_key

    phase = "entry_intent" if side == "buy" else "exit_intent"
    print(
        "[TRADE_LIFECYCLE] "
        f"phase={phase} symbol={symbol} side={side} "
        f"quantity={qty:.12f} fill_price={price:.12f} "
        f"source={source} strategy={strategy} "
        f"position_qty_before={position_before:.12f} "
        f"cost_basis_used={cost_basis_before:.12f} "
        f"lifecycle_key={lifecycle_key}",
        flush=True,
    )

    try:
        result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized, source=source)
    except TypeError:
        result = _QFOS_ATOMIC_FILL_CORE_V1(conn, normalized)

    if result is None or result is False:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            "reason=atomic_core_rejected",
            flush=True,
        )
        return False

    try:
        trade = conn.execute(text("""
            SELECT id, quantity, fill_price, pnl, is_exit, exit_reason
            FROM trades
            WHERE symbol = :symbol
              AND lower(side) = :side
            ORDER BY id DESC
            LIMIT 1
        """), {"symbol": symbol, "side": side}).mappings().first()

        pos = conn.execute(text("""
            SELECT quantity, avg_entry
            FROM positions
            WHERE symbol = :symbol
            LIMIT 1
        """), {"symbol": symbol}).mappings().first()

        trade_id = (trade or {}).get("id")
        realized_pnl = float((trade or {}).get("pnl") or 0.0)
        position_after = float((pos or {}).get("quantity") or 0.0)
        cost_basis_after = float((pos or {}).get("avg_entry") or 0.0)

        phase = "buy_persisted" if side == "buy" else "sell_persisted"
        print(
            "[TRADE_LIFECYCLE] "
            f"phase={phase} trade_id={trade_id} symbol={symbol} side={side} "
            f"quantity={qty:.12f} fill_price={price:.12f} "
            f"notional={qty * price:.12f} source={source} strategy={strategy} "
            f"is_exit={int(bool((trade or {}).get('is_exit')))} "
            f"exit_reason={str((trade or {}).get('exit_reason') or '')} "
            f"cost_basis_used={cost_basis_before:.12f} "
            f"realized_pnl={realized_pnl:.12f} "
            f"position_qty_before={position_before:.12f} "
            f"position_qty_after={position_after:.12f} "
            f"position_cost_basis_after={cost_basis_after:.12f}",
            flush=True,
        )

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol} side={side} "
            f"reason=after_persist_probe_error:{e}",
            flush=True,
        )

    return result


# Redirect all existing persistence callers through canonical wrapper.
qfos_persist_fill_atomic = qfos_apply_fill_atomic


def apply_buy(fill):
    """
    Legacy compatibility only.
    Real BUYs must not mutate cash or positions before atomic persistence.
    """
    try:
        symbol = str(fill.get("symbol") or "")
        qty = float(fill.get("quantity") or fill.get("qty") or 0.0)
        price = float(fill.get("fill_price") or fill.get("expected_price") or 0.0)

        if not symbol or qty <= 0 or price <= 0:
            print(
                f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=buy "
                "reason=legacy_apply_buy_invalid_fill",
                flush=True,
            )
            return False

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=entry_intent source=legacy_apply_buy_adapter "
            f"symbol={symbol} side=buy quantity={qty:.12f} "
            f"fill_price={price:.12f} action=no_runtime_mutation",
            flush=True,
        )
        return True

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol=UNKNOWN side=buy "
            f"reason=legacy_apply_buy_adapter_error:{e}",
            flush=True,
        )
        return False


def apply_sell(symbol, qty, price, reason):
    """
    Legacy compatibility only.
    Build SELL intent from DB-backed runtime cache without changing cash,
    position quantity, cost basis, or lifecycle state before persistence.
    """
    try:
        symbol = str(symbol or "").strip()
        requested_qty = float(qty or 0.0)
        fill_price = float(price or 0.0)
        reason = str(reason or "unknown").strip()

        held = float(portfolio.positions.get(symbol, 0.0) or 0.0)
        sell_qty = min(requested_qty, held)

        if not symbol or sell_qty <= 0 or fill_price <= 0:
            print(
                f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=sell "
                "reason=legacy_apply_sell_invalid_or_no_db_backed_position",
                flush=True,
            )
            return None

        print(
            "[TRADE_LIFECYCLE] "
            f"phase=exit_intent source=legacy_apply_sell_adapter "
            f"symbol={symbol} side=sell quantity={sell_qty:.12f} "
            f"fill_price={fill_price:.12f} exit_reason={reason} "
            "action=no_runtime_mutation",
            flush=True,
        )

        return {
            "symbol": symbol,
            "side": "sell",
            "quantity": sell_qty,
            "qty": sell_qty,
            "expected_price": fill_price,
            "fill_price": fill_price,
            "slippage_bps": 0.0,
            "strategy": reason,
            "reason": reason,
            "is_exit": True,
            "exit_reason": reason,
            "confidence": 1.0,
            "live": False,
            "shadow_mode": False,
            "source": "legacy_apply_sell_adapter",
        }

    except Exception as e:
        print(
            f"[TRADE_BOUNDARY_REJECT] symbol={symbol or 'UNKNOWN'} side=sell "
            f"reason=legacy_apply_sell_adapter_error:{e}",
            flush=True,
        )
        return None


def qfos_db_sync_positions_from_portfolio(conn, portfolio_obj, prices):
    """
    Compatibility replacement for legacy memory-to-DB synchronizer.
    DB is authoritative. This function now only refreshes runtime cache.
    """
    print(
        "[TRADE_LIFECYCLE] "
        "phase=position_sync direction=db_to_runtime "
        "legacy_memory_to_db_write=disabled",
        flush=True,
    )
    qfos_refresh_runtime_portfolio_from_db(source="legacy_sync_adapter")
    return True


def update_position_from_fill(conn, fill, prices=None):
    """
    Compatibility replacement.
    Position quantity/cost basis are rebuilt by DB trade trigger.
    """
    print(
        "[TRADE_LIFECYCLE] "
        "phase=position_updated authority=trade_trigger "
        "legacy_update_position_from_fill=disabled",
        flush=True,
    )
    qfos_refresh_runtime_portfolio_from_db(source="legacy_update_position_adapter")
    return True

# ============================================================
# END QFOS_CANONICAL_TRADE_LIFECYCLE_V1
# ============================================================

'''

src = src[:idx] + block + "\n" + src[idx:]
path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
