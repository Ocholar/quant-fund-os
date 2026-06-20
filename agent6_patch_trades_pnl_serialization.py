from pathlib import Path
import re

path = Path("services/api.py")
src = path.read_text(encoding="utf-8")

patch = r'''
# ============================================================
# AGENT6_TRADES_PNL_SERIALIZATION_PATCH_V1
#
# Purpose:
#   /trades must expose id, pnl, and notional from Postgres.
#
# Scope:
#   API serialization only.
#   Does not touch execution, allocator, risk, cash/equity authority,
#   ledger writes, or strategy logic.
# ============================================================

def _agent6_trade_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _agent6_trade_bool(value):
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "exit"}
    except Exception:
        return False


def _agent6_trade_iso(value):
    try:
        return value.isoformat() if hasattr(value, "isoformat") else value
    except Exception:
        return value


def _agent6_trade_to_dashboard_dict(row):
    d = dict(row)

    strategy = str(d.get("strategy") or "").strip()
    side = str(d.get("side") or "").strip().lower()

    qty = _agent6_trade_float(d.get("quantity"), 0.0)
    price = _agent6_trade_float(d.get("fill_price"), 0.0)

    raw_is_exit = d.get("is_exit")
    exit_reason = d.get("exit_reason")

    strategy_l = strategy.lower()
    exit_words = (
        "stop_loss",
        "take_profit",
        "risk_off",
        "emergency",
        "breakeven",
        "time_stop",
        "exit",
        "trailing_stop",
        "manual_exit",
        "liquidation",
    )

    inferred_is_exit = (
        side == "sell"
        and (
            _agent6_trade_bool(raw_is_exit)
            or bool(exit_reason)
            or any(word in strategy_l for word in exit_words)
        )
    )

    if inferred_is_exit and not exit_reason:
        exit_reason = strategy

    pnl = d.get("pnl")
    if pnl is None:
        pnl = 0.0

    out = {
        "id": d.get("id"),
        "time": _agent6_trade_iso(d.get("created_at")),
        "created_at": _agent6_trade_iso(d.get("created_at")),
        "symbol": d.get("symbol"),
        "side": side,
        "quantity": qty,
        "fill_price": price,
        "notional": round(qty * price, 10),
        "slippage_bps": _agent6_trade_float(d.get("slippage_bps"), 0.0),
        "strategy": strategy,
        "raw_strategy": strategy,
        "entry_strategy": None if inferred_is_exit else strategy,
        "display_strategy": str(exit_reason or strategy),
        "confidence": _agent6_trade_float(d.get("confidence"), 0.0),
        "live": _agent6_trade_bool(d.get("live")),
        "is_exit": bool(inferred_is_exit),
        "exit_reason": str(exit_reason) if exit_reason else None,
        "pnl": _agent6_trade_float(pnl, 0.0),
    }

    return out


@app.get("/trades")
def trades(limit: int = 50):
    try:
        limit = int(limit or 50)
    except Exception:
        limit = 50

    if limit < 1:
        limit = 50
    if limit > 500:
        limit = 500

    with engine.begin() as conn:
        qfo_status_preensure_trades_table(conn)

        rows = conn.execute(text("""
            SELECT
                id,
                symbol,
                side,
                quantity,
                fill_price,
                quantity * fill_price AS notional,
                slippage_bps,
                strategy,
                confidence,
                live,
                is_exit,
                exit_reason,
                pnl,
                created_at
            FROM trades
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

    items = [_agent6_trade_to_dashboard_dict(r) for r in rows]

    return {
        "value": items,
        "trades": items,
        "latest_trades": items,
        "Count": len(items),
        "count": len(items),
    }

# ============================================================
# END AGENT6_TRADES_PNL_SERIALIZATION_PATCH_V1
# ============================================================

'''

pattern = r'@app\.get\("/trades"\)\s*def\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\):.*?(?=\n@app\.)'

src, n = re.subn(pattern, patch + "\n\n", src, flags=re.S)

if n < 1:
    raise SystemExit("Could not find /trades route to replace.")

if "AGENT6_TRADES_PNL_SERIALIZATION_PATCH_V1" not in src:
    raise SystemExit("Patch marker missing after replacement.")

path.write_text(src, encoding="utf-8")

print(f"PATCH_OK trades_routes_replaced={n}")
