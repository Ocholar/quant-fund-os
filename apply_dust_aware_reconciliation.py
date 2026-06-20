from pathlib import Path

path = Path("services/api.py")
text = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_DUST_AWARE_RECONCILIATION_V1"

if marker in text:
    print("DUST_RECONCILIATION_PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_DUST_AWARE_RECONCILIATION_V1
# This final response overlay distinguishes harmless historical dust from
# a real unmatched SELL / residual inventory defect. It never changes DB rows,
# positions, fills, strategy logic, orders, cash, or risk state.

_qfos_dust_reconciliation_cache_v1 = {
    "at": 0.0,
    "value": None,
    "engine": None,
}

def _qfos_dust_aware_fill_audit_v1():
    import os
    import time
    from sqlalchemy import create_engine, text

    now = time.monotonic()
    cached = _qfos_dust_reconciliation_cache_v1.get("value")

    if cached is not None and (now - _qfos_dust_reconciliation_cache_v1["at"]) < 2.0:
        return cached

    try:
        engine = _qfos_dust_reconciliation_cache_v1.get("engine")

        if engine is None:
            db_url = os.getenv("DATABASE_URL")

            if not db_url:
                raise RuntimeError("DATABASE_URL missing")

            engine = create_engine(db_url, pool_pre_ping=True)
            _qfos_dust_reconciliation_cache_v1["engine"] = engine

        with engine.connect() as conn:
            columns = {
                str(row["column_name"]).lower()
                for row in conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'trades'
                """)).mappings().all()
            }

            price_col = next(
                (
                    name for name in ("fill_price", "expected_price", "price")
                    if name in columns
                ),
                None,
            )

            if price_col is None:
                raise RuntimeError("No persisted fill-price column found")

            rows = conn.execute(text(f"""
                SELECT
                    id,
                    symbol,
                    LOWER(COALESCE(side, '')) AS side,
                    COALESCE(quantity, 0) AS quantity,
                    COALESCE({price_col}, 0) AS fill_price
                FROM trades
                ORDER BY id ASC
            """)).mappings().all()

        epsilon = 1e-10
        dust_notional_limit = 0.01

        inventory = {}
        last_price = {}
        outcomes = []

        unmatched_fragments = []
        residuals = []

        for row in rows:
            try:
                symbol = str(row["symbol"] or "")
                side = str(row["side"] or "").strip().lower()
                quantity = abs(float(row["quantity"] or 0.0))
                price = float(row["fill_price"] or 0.0)
            except Exception:
                continue

            if not symbol or quantity <= epsilon or price <= 0:
                continue

            last_price[symbol] = price
            held_qty, average_entry = inventory.get(symbol, (0.0, 0.0))

            if side == "buy":
                next_qty = held_qty + quantity
                next_avg = (
                    ((held_qty * average_entry) + (quantity * price)) / next_qty
                    if next_qty > epsilon else 0.0
                )
                inventory[symbol] = (next_qty, next_avg)
                continue

            if side != "sell":
                continue

            close_qty = min(quantity, held_qty)

            if close_qty > epsilon:
                outcomes.append((price - average_entry) * close_qty)

            excess_qty = quantity - close_qty

            if excess_qty > epsilon:
                unmatched_fragments.append({
                    "symbol": symbol,
                    "quantity": excess_qty,
                    "price": price,
                    "notional": excess_qty * price,
                })

            remaining_qty = held_qty - close_qty

            inventory[symbol] = (
                (remaining_qty, average_entry)
                if remaining_qty > epsilon
                else (0.0, 0.0)
            )

        for symbol, (quantity, average_entry) in inventory.items():
            if quantity <= epsilon:
                continue

            price = last_price.get(symbol, average_entry)
            residuals.append({
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "notional": quantity * price,
            })

        wins = sum(1 for pnl in outcomes if pnl > epsilon)
        losses = sum(1 for pnl in outcomes if pnl < -epsilon)
        breakevens = sum(1 for pnl in outcomes if abs(pnl) <= epsilon)

        unmatched_dust = [
            row for row in unmatched_fragments
            if row["notional"] <= dust_notional_limit
        ]
        unmatched_non_dust = [
            row for row in unmatched_fragments
            if row["notional"] > dust_notional_limit
        ]

        residual_dust = [
            row for row in residuals
            if row["notional"] <= dust_notional_limit
        ]
        residual_non_dust = [
            row for row in residuals
            if row["notional"] > dust_notional_limit
        ]

        result = {
            "available": True,
            "basis": (
                "chronological_weighted_average_fill_prices_"
                "excluding_unrecorded_fees"
            ),
            "dust_notional_limit": dust_notional_limit,
            "closed_count": len(outcomes),
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "raw_realized_pnl": round(sum(outcomes), 8),
            "win_rate": (wins / len(outcomes)) if outcomes else 0.0,
            "unmatched_sell_count": len(unmatched_fragments),
            "unmatched_sell_notional": round(
                sum(row["notional"] for row in unmatched_fragments), 12
            ),
            "unmatched_sell_dust_count": len(unmatched_dust),
            "unmatched_sell_non_dust_count": len(unmatched_non_dust),
            "unmatched_sell_non_dust_notional": round(
                sum(row["notional"] for row in unmatched_non_dust), 12
            ),
            "residual_inventory_count": len(residuals),
            "residual_inventory_notional": round(
                sum(row["notional"] for row in residuals), 12
            ),
            "residual_inventory_dust_count": len(residual_dust),
            "residual_inventory_non_dust_count": len(residual_non_dust),
            "residual_inventory_non_dust_notional": round(
                sum(row["notional"] for row in residual_non_dust), 12
            ),
        }

    except Exception as exc:
        result = {
            "available": False,
            "error": repr(exc),
        }

    _qfos_dust_reconciliation_cache_v1["at"] = now
    _qfos_dust_reconciliation_cache_v1["value"] = result
    return result


@app.middleware("http")
async def _qfos_dust_aware_reconciliation_v1(request, call_next):
    response = await call_next(request)

    if request.url.path != "/status":
        return response

    try:
        import json
        from fastapi.responses import Response

        body = b""

        async for chunk in response.body_iterator:
            body += chunk

        payload = json.loads(body.decode("utf-8"))

        portfolio = payload.get("portfolio")
        performance = payload.get("performance")

        if not isinstance(portfolio, dict):
            portfolio = {}
            payload["portfolio"] = portfolio

        if not isinstance(performance, dict):
            performance = {}
            payload["performance"] = performance

        audit = _qfos_dust_aware_fill_audit_v1()

        if not audit.get("available"):
            performance["metrics_available"] = False
            performance["metrics_reconciliation_gate"] = "BLOCKED"
            performance["metrics_error"] = (
                "dust_aware_audit_failed:"
                + str(audit.get("error"))
            )
        else:
            ledger_realized = float(portfolio.get("realized_pnl") or 0.0)
            raw_realized = float(audit["raw_realized_pnl"])
            delta = round(raw_realized - ledger_realized, 8)
            tolerance = 0.01

            non_dust_unmatched = int(
                audit["unmatched_sell_non_dust_count"]
            )
            non_dust_residuals = int(
                audit["residual_inventory_non_dust_count"]
            )

            reconciled = (
                abs(delta) <= tolerance
                and non_dust_unmatched == 0
                and non_dust_residuals == 0
            )

            performance["trade_pnl_field_ignored"] = True
            performance["metrics_basis"] = audit["basis"]
            performance["ledger_realized_pnl"] = ledger_realized
            performance["fill_derived_closed_pnl"] = raw_realized
            performance["gross_fill_price_realized_pnl"] = raw_realized
            performance["metrics_reconciliation_delta"] = delta
            performance["metrics_reconciliation_tolerance"] = tolerance
            performance["metrics_reconciliation_gate"] = (
                "PASS" if reconciled else "BLOCKED"
            )

            performance["closed_outcome_count"] = audit["closed_count"]
            performance["winning_closed_fills"] = audit["wins"]
            performance["losing_closed_fills"] = audit["losses"]
            performance["breakeven_closed_fills"] = audit["breakevens"]
            performance["unmatched_sell_fills"] = audit["unmatched_sell_count"]

            performance["dust_notional_limit"] = audit["dust_notional_limit"]
            performance["unmatched_sell_notional"] = audit["unmatched_sell_notional"]
            performance["unmatched_sell_dust_count"] = audit["unmatched_sell_dust_count"]
            performance["unmatched_sell_non_dust_count"] = audit["unmatched_sell_non_dust_count"]
            performance["unmatched_sell_non_dust_notional"] = audit["unmatched_sell_non_dust_notional"]

            performance["residual_inventory_count"] = audit["residual_inventory_count"]
            performance["residual_inventory_notional"] = audit["residual_inventory_notional"]
            performance["residual_inventory_dust_count"] = audit["residual_inventory_dust_count"]
            performance["residual_inventory_non_dust_count"] = audit["residual_inventory_non_dust_count"]
            performance["residual_inventory_non_dust_notional"] = audit["residual_inventory_non_dust_notional"]

            if reconciled:
                rate = round(float(audit["win_rate"]), 4)
                performance["metrics_available"] = True
                performance["metrics_error"] = None
                performance["win_rate"] = rate
                performance["win_rate_estimate"] = rate
            else:
                performance["metrics_available"] = False
                performance["win_rate"] = None
                performance["win_rate_estimate"] = None
                performance["metrics_error"] = (
                    "dust_aware_reconciliation_blocked: "
                    f"delta={delta}; "
                    f"non_dust_unmatched={non_dust_unmatched}; "
                    f"non_dust_residuals={non_dust_residuals}"
                )

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() != "content-length"
        }

        return Response(
            content=json.dumps(payload, default=str).encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=response.background,
        )

    except Exception as exc:
        print(
            f"[QFOS_DUST_AWARE_RECONCILIATION_ERROR] error={exc!r}",
            flush=True,
        )
        return response
'''

path.write_text(text + patch, encoding="utf-8")
print("DUST_RECONCILIATION_PATCH_OK")