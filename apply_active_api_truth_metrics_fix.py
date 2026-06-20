from pathlib import Path

path = Path("services/api.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_TRUTHFUL_FILL_METRICS_ACTIVE_API_V1"

if marker in src:
    print("ACTIVE_API_TRUTH_METRICS_ALREADY_PRESENT")
    raise SystemExit(0)

anchor = "\ndef get_status_payload():"
if anchor not in src:
    raise RuntimeError("PATCH_FAILED: active API anchor 'def get_status_payload()' not found.")

patch = r'''

# QFOS_TRUTHFUL_FILL_METRICS_ACTIVE_API_V1
# Presentation metrics only. This changes neither orders nor portfolio accounting.

def qfos_truthful_fill_metrics(conn):
    eps = 1e-10

    try:
        column_rows = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'trades'
        """)).mappings().all()

        columns = {str(r["column_name"]).lower() for r in column_rows}
    except Exception as exc:
        return {
            "metrics_available": False,
            "metrics_basis": "unavailable",
            "metrics_error": f"trade_schema_read_failed:{exc!r}",
        }

    price_column = next(
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

    if price_column is None:
        return {
            "metrics_available": False,
            "metrics_basis": "unavailable",
            "metrics_error": "no_supported_fill_price_column",
        }

    pnl_expression = "NULL"
    if "pnl" in columns:
        pnl_expression = "pnl"

    try:
        rows = conn.execute(text(f"""
            SELECT
                id,
                symbol,
                LOWER(COALESCE(side, '')) AS side,
                COALESCE(quantity, 0) AS quantity,
                COALESCE({price_column}, 0) AS price,
                {pnl_expression} AS persisted_pnl
            FROM trades
            ORDER BY id ASC
        """)).mappings().all()
    except Exception as exc:
        return {
            "metrics_available": False,
            "metrics_basis": "unavailable",
            "metrics_error": f"trade_read_failed:{exc!r}",
        }

    inventory = {}
    outcomes = []
    unmatched_sell_fills = 0
    persisted_sell_pnl_seen = False

    for row in rows:
        try:
            symbol = str(row["symbol"] or "")
            side = str(row["side"] or "").lower()
            quantity = abs(float(row["quantity"] or 0))
            price = float(row["price"] or 0)
        except Exception:
            continue

        if not symbol or quantity <= eps or price <= 0:
            continue

        current_qty, avg_entry = inventory.get(symbol, (0.0, 0.0))

        if side == "buy":
            next_qty = current_qty + quantity
            next_avg_entry = (
                ((current_qty * avg_entry) + (quantity * price)) / next_qty
                if next_qty > eps
                else 0.0
            )
            inventory[symbol] = (next_qty, next_avg_entry)
            continue

        if side != "sell":
            continue

        if current_qty <= eps:
            unmatched_sell_fills += 1
            continue

        close_qty = min(quantity, current_qty)
        fill_price_pnl = (price - avg_entry) * close_qty

        persisted_pnl = row.get("persisted_pnl")
        try:
            persisted_pnl = float(persisted_pnl) if persisted_pnl is not None else None
        except Exception:
            persisted_pnl = None

        if persisted_pnl is not None and abs(persisted_pnl) > eps:
            realized_outcome = persisted_pnl
            persisted_sell_pnl_seen = True
        else:
            realized_outcome = fill_price_pnl

        outcomes.append(realized_outcome)

        remaining_qty = current_qty - close_qty
        inventory[symbol] = (
            (remaining_qty, avg_entry)
            if remaining_qty > eps
            else (0.0, 0.0)
        )

    wins = sum(1 for value in outcomes if value > eps)
    losses = sum(1 for value in outcomes if value < -eps)
    breakevens = sum(1 for value in outcomes if abs(value) <= eps)
    outcome_count = len(outcomes)

    return {
        "metrics_available": True,
        "metrics_basis": (
            "persisted_sell_pnl_when_available_otherwise_weighted_average_fill_price"
            if persisted_sell_pnl_seen
            else "weighted_average_fill_price_before_fees"
        ),
        "closed_outcome_count": outcome_count,
        "winning_closed_fills": wins,
        "losing_closed_fills": losses,
        "breakeven_closed_fills": breakevens,
        "unmatched_sell_fills": unmatched_sell_fills,
        "truthful_win_rate": (wins / outcome_count) if outcome_count else 0.0,
        "fill_derived_closed_pnl": round(sum(outcomes), 8),
    }


# Preserve the existing performance calculations, then override only
# outcome-count and win-rate fields with fill-derived closed-trade truth.
_qfos_get_performance_before_truth_metrics = get_performance

def get_performance(conn, equity):
    performance = _qfos_get_performance_before_truth_metrics(conn, equity)

    try:
        truth = qfos_truthful_fill_metrics(conn)
    except Exception as exc:
        truth = {
            "metrics_available": False,
            "metrics_basis": "unavailable",
            "metrics_error": repr(exc),
        }

    if truth.get("metrics_available"):
        performance["win_rate"] = round(float(truth["truthful_win_rate"]), 4)
        performance["win_rate_estimate"] = round(float(truth["truthful_win_rate"]), 4)
        performance["closed_outcome_count"] = int(truth["closed_outcome_count"])
        performance["winning_closed_fills"] = int(truth["winning_closed_fills"])
        performance["losing_closed_fills"] = int(truth["losing_closed_fills"])
        performance["breakeven_closed_fills"] = int(truth["breakeven_closed_fills"])
        performance["unmatched_sell_fills"] = int(truth["unmatched_sell_fills"])
        performance["metrics_basis"] = truth["metrics_basis"]
        performance["fill_derived_closed_pnl"] = float(truth["fill_derived_closed_pnl"])
    else:
        performance["metrics_basis"] = truth.get("metrics_basis", "unavailable")
        performance["metrics_error"] = truth.get("metrics_error", "unknown")

    return performance
'''

src = src.replace(anchor, patch + anchor, 1)
path.write_text(src, encoding="utf-8")

print("ACTIVE_API_TRUTH_METRICS_PATCH_WRITE_OK")
