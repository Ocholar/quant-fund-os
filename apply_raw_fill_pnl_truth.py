from pathlib import Path

path = Path("services/api.py")
text = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_RAW_FILL_PNL_TRUTH_V1"

if marker in text:
    print("RAW_FILL_PNL_PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_RAW_FILL_PNL_TRUTH_V1
# The stored trades.pnl field is not trusted for headline performance because
# it does not consistently equal the economic difference between BUY and SELL
# fills. This computes realized PnL from chronological persisted fill prices.
# Presentation-only: no orders, positions, cash, or database rows are changed.

_qfos_raw_fill_pnl_cache_v1 = {
    "at": 0.0,
    "value": None,
    "engine": None,
}

def _qfos_raw_fill_pnl_truth_v1():
    import os
    import time
    from sqlalchemy import create_engine, text

    now = time.monotonic()
    cached = _qfos_raw_fill_pnl_cache_v1.get("value")

    if cached is not None and (now - _qfos_raw_fill_pnl_cache_v1["at"]) < 2.0:
        return cached

    try:
        engine = _qfos_raw_fill_pnl_cache_v1.get("engine")

        if engine is None:
            db_url = os.getenv("DATABASE_URL")

            if not db_url:
                raise RuntimeError("DATABASE_URL missing")

            engine = create_engine(db_url, pool_pre_ping=True)
            _qfos_raw_fill_pnl_cache_v1["engine"] = engine

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
                    name for name in (
                        "fill_price",
                        "expected_price",
                        "price",
                    )
                    if name in columns
                ),
                None,
            )

            if price_col is None:
                raise RuntimeError("No fill-price column available")

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
        inventory = {}
        outcome_pnls = []
        unmatched_sell_fills = 0
        unmatched_sell_quantity = 0.0

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

            held_quantity, average_entry = inventory.get(symbol, (0.0, 0.0))

            if side == "buy":
                new_quantity = held_quantity + quantity
                new_average = (
                    ((held_quantity * average_entry) + (quantity * price))
                    / new_quantity
                    if new_quantity > epsilon
                    else 0.0
                )
                inventory[symbol] = (new_quantity, new_average)
                continue

            if side != "sell":
                continue

            if held_quantity <= epsilon:
                unmatched_sell_fills += 1
                unmatched_sell_quantity += quantity
                continue

            closing_quantity = min(quantity, held_quantity)
            realized = (price - average_entry) * closing_quantity
            outcome_pnls.append(realized)

            remaining_quantity = held_quantity - closing_quantity

            inventory[symbol] = (
                (remaining_quantity, average_entry)
                if remaining_quantity > epsilon
                else (0.0, 0.0)
            )

            if quantity - closing_quantity > epsilon:
                unmatched_sell_fills += 1
                unmatched_sell_quantity += quantity - closing_quantity

        wins = sum(1 for value in outcome_pnls if value > epsilon)
        losses = sum(1 for value in outcome_pnls if value < -epsilon)
        breakevens = sum(1 for value in outcome_pnls if abs(value) <= epsilon)
        closed = len(outcome_pnls)

        open_inventory_symbols = sum(
            1 for quantity, _ in inventory.values()
            if quantity > epsilon
        )

        result = {
            "raw_fill_metrics_available": True,
            "raw_fill_metrics_basis": (
                "chronological_weighted_average_buy_sell_fill_prices_"
                "excluding_unrecorded_fees"
            ),
            "raw_fill_closed_outcome_count": closed,
            "raw_fill_winning_closed_fills": wins,
            "raw_fill_losing_closed_fills": losses,
            "raw_fill_breakeven_closed_fills": breakevens,
            "raw_fill_unmatched_sell_fills": unmatched_sell_fills,
            "raw_fill_unmatched_sell_quantity": round(unmatched_sell_quantity, 12),
            "raw_fill_open_inventory_symbols": open_inventory_symbols,
            "raw_fill_realized_pnl": round(sum(outcome_pnls), 8),
            "raw_fill_win_rate": (wins / closed) if closed else 0.0,
        }

    except Exception as exc:
        result = {
            "raw_fill_metrics_available": False,
            "raw_fill_metrics_basis": "unavailable",
            "raw_fill_metrics_error": repr(exc),
            "raw_fill_closed_outcome_count": None,
            "raw_fill_winning_closed_fills": None,
            "raw_fill_losing_closed_fills": None,
            "raw_fill_breakeven_closed_fills": None,
            "raw_fill_unmatched_sell_fills": None,
            "raw_fill_unmatched_sell_quantity": None,
            "raw_fill_open_inventory_symbols": None,
            "raw_fill_realized_pnl": None,
            "raw_fill_win_rate": None,
        }

    _qfos_raw_fill_pnl_cache_v1["at"] = now
    _qfos_raw_fill_pnl_cache_v1["value"] = result
    return result


@app.middleware("http")
async def _qfos_raw_fill_pnl_truth_v1_middleware(request, call_next):
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

        truth = _qfos_raw_fill_pnl_truth_v1()

        performance["trade_pnl_field_ignored"] = True
        performance["trade_pnl_field_note"] = (
            "Persisted trades.pnl is excluded from headline metrics because "
            "it does not consistently match BUY/SELL fill economics."
        )

        performance["raw_fill_metrics_available"] = truth["raw_fill_metrics_available"]
        performance["raw_fill_metrics_basis"] = truth["raw_fill_metrics_basis"]
        performance["raw_fill_metrics_error"] = truth.get("raw_fill_metrics_error")
        performance["raw_fill_closed_outcome_count"] = truth["raw_fill_closed_outcome_count"]
        performance["raw_fill_winning_closed_fills"] = truth["raw_fill_winning_closed_fills"]
        performance["raw_fill_losing_closed_fills"] = truth["raw_fill_losing_closed_fills"]
        performance["raw_fill_breakeven_closed_fills"] = truth["raw_fill_breakeven_closed_fills"]
        performance["raw_fill_unmatched_sell_fills"] = truth["raw_fill_unmatched_sell_fills"]
        performance["raw_fill_unmatched_sell_quantity"] = truth["raw_fill_unmatched_sell_quantity"]
        performance["raw_fill_open_inventory_symbols"] = truth["raw_fill_open_inventory_symbols"]
        performance["raw_fill_realized_pnl"] = truth["raw_fill_realized_pnl"]
        performance["raw_fill_win_rate"] = truth["raw_fill_win_rate"]

        try:
            ledger_realized = float(portfolio.get("realized_pnl") or 0.0)
            raw_fill_realized = float(truth["raw_fill_realized_pnl"])
            delta = round(raw_fill_realized - ledger_realized, 8)
        except Exception:
            ledger_realized = None
            raw_fill_realized = None
            delta = None

        tolerance = 0.01

        reconciled = (
            bool(truth["raw_fill_metrics_available"])
            and delta is not None
            and abs(delta) <= tolerance
            and int(truth["raw_fill_unmatched_sell_fills"] or 0) == 0
        )

        performance["ledger_realized_pnl"] = ledger_realized
        performance["fill_derived_closed_pnl"] = raw_fill_realized
        performance["gross_fill_price_realized_pnl"] = raw_fill_realized
        performance["metrics_reconciliation_delta"] = delta
        performance["metrics_reconciliation_tolerance"] = tolerance
        performance["metrics_reconciliation_gate"] = (
            "PASS" if reconciled else "BLOCKED"
        )

        performance["closed_outcome_count"] = truth["raw_fill_closed_outcome_count"]
        performance["winning_closed_fills"] = truth["raw_fill_winning_closed_fills"]
        performance["losing_closed_fills"] = truth["raw_fill_losing_closed_fills"]
        performance["breakeven_closed_fills"] = truth["raw_fill_breakeven_closed_fills"]
        performance["unmatched_sell_fills"] = truth["raw_fill_unmatched_sell_fills"]

        if reconciled:
            rate = round(float(truth["raw_fill_win_rate"]), 4)
            performance["metrics_available"] = True
            performance["metrics_basis"] = truth["raw_fill_metrics_basis"]
            performance["metrics_error"] = None
            performance["win_rate"] = rate
            performance["win_rate_estimate"] = rate
        else:
            performance["metrics_available"] = False
            performance["metrics_basis"] = truth["raw_fill_metrics_basis"]
            performance["metrics_error"] = (
                "raw_fill_reconciliation_blocked: "
                f"ledger_realized={ledger_realized}; "
                f"raw_fill_realized={raw_fill_realized}; "
                f"delta={delta}; tolerance={tolerance}; "
                f"unmatched_sells={truth['raw_fill_unmatched_sell_fills']}"
            )
            performance["win_rate"] = None
            performance["win_rate_estimate"] = None

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
            f"[QFOS_RAW_FILL_PNL_TRUTH_ERROR] error={exc!r}",
            flush=True,
        )
        return response
'''

path.write_text(text + patch, encoding="utf-8")
print("RAW_FILL_PNL_PATCH_OK")