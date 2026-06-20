from pathlib import Path

# ============================================================
# 1. Patch start.sh: API must confirm PAUSED before main.py.
# ============================================================
start_path = Path("start.sh")
start = start_path.read_text(encoding="utf-8", errors="replace")

startup_marker = "# QFOS_STARTUP_PAUSE_INTERLOCK_V3"

if startup_marker not in start:
    old = (
        "python -m uvicorn services.api:app --host 0.0.0.0 --port 8080 &\n"
        "python main.py"
    )

    if old not in start:
        raise SystemExit(
            "PATCH_FAILED: expected startup tail not found in start.sh"
        )

    new = r'''python -m uvicorn services.api:app --host 0.0.0.0 --port 8080 &
API_PID=$!

# QFOS_STARTUP_PAUSE_INTERLOCK_V3
# Fail closed: main.py cannot start unless the API confirms PAUSED.
python - <<'PY'
import time
import urllib.request

last_error = None

for _ in range(40):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8080/pause",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            if 200 <= response.status < 300:
                print("[STARTUP_PAUSE_INTERLOCK] pause_confirmed", flush=True)
                break
    except Exception as exc:
        last_error = repr(exc)
        time.sleep(0.25)
else:
    raise SystemExit(
        f"[STARTUP_PAUSE_INTERLOCK] pause_failed error={last_error}"
    )
PY

python main.py'''

    start = start.replace(old, new, 1)
    start_path.write_text(start, encoding="utf-8")
    print("START_SH_PATCH_OK")
else:
    print("START_SH_PATCH_ALREADY_PRESENT")

# ============================================================
# 2. Patch the active port-8080 FastAPI service.
# ============================================================
api_path = Path("services/api.py")
api = api_path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_ACTIVE_API_TRUTH_METRICS_V3"

if marker not in api:
    patch = r'''

# QFOS_ACTIVE_API_TRUTH_METRICS_V3
# Active /status contract. Display-only; it does not alter trades,
# positions, cash, strategy logic, or risk decisions.

_qfos_truth_metrics_v3_cache = {
    "at": 0.0,
    "value": None,
    "engine": None,
}

def _qfos_active_truth_metrics_v3():
    import os
    import time
    from sqlalchemy import create_engine, text

    now = time.monotonic()
    cached = _qfos_truth_metrics_v3_cache.get("value")

    if cached is not None and (now - _qfos_truth_metrics_v3_cache["at"]) < 2.0:
        return cached

    try:
        engine = _qfos_truth_metrics_v3_cache.get("engine")

        if engine is None:
            db_url = os.getenv("DATABASE_URL")

            if not db_url:
                raise RuntimeError("DATABASE_URL missing")

            engine = create_engine(db_url, pool_pre_ping=True)
            _qfos_truth_metrics_v3_cache["engine"] = engine

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
                    value for value in (
                        "fill_price",
                        "expected_price",
                        "price",
                    )
                    if value in columns
                ),
                None,
            )

            if not price_col:
                raise RuntimeError("No persisted fill-price column found")

            pnl_col = "pnl" if "pnl" in columns else "NULL"

            rows = conn.execute(text(f"""
                SELECT
                    id,
                    symbol,
                    LOWER(COALESCE(side, '')) AS side,
                    COALESCE(quantity, 0) AS quantity,
                    COALESCE({price_col}, 0) AS price,
                    {pnl_col} AS persisted_pnl
                FROM trades
                ORDER BY id ASC
            """)).mappings().all()

        eps = 1e-10
        inventory = {}
        outcomes = []
        unmatched_sells = 0
        used_persisted_pnl = False

        for row in rows:
            try:
                symbol = str(row["symbol"] or "")
                side = str(row["side"] or "").strip().lower()
                quantity = abs(float(row["quantity"] or 0))
                price = float(row["price"] or 0)
            except Exception:
                continue

            if not symbol or quantity <= eps or price <= 0:
                continue

            owned_qty, average_entry = inventory.get(symbol, (0.0, 0.0))

            if side == "buy":
                next_qty = owned_qty + quantity
                next_avg = (
                    ((owned_qty * average_entry) + (quantity * price)) / next_qty
                    if next_qty > eps else 0.0
                )
                inventory[symbol] = (next_qty, next_avg)
                continue

            if side != "sell":
                continue

            if owned_qty <= eps:
                unmatched_sells += 1
                continue

            closing_qty = min(quantity, owned_qty)
            derived_pnl = (price - average_entry) * closing_qty

            try:
                persisted_pnl = (
                    float(row["persisted_pnl"])
                    if row["persisted_pnl"] is not None
                    else None
                )
            except Exception:
                persisted_pnl = None

            if persisted_pnl is not None and abs(persisted_pnl) > eps:
                outcome = persisted_pnl
                used_persisted_pnl = True
            else:
                outcome = derived_pnl

            outcomes.append(outcome)

            remaining = owned_qty - closing_qty
            inventory[symbol] = (
                (remaining, average_entry)
                if remaining > eps else (0.0, 0.0)
            )

        wins = sum(1 for value in outcomes if value > eps)
        losses = sum(1 for value in outcomes if value < -eps)
        breakevens = sum(1 for value in outcomes if abs(value) <= eps)
        closed = len(outcomes)

        result = {
            "metrics_available": True,
            "metrics_basis": (
                "persisted_sell_pnl_when_nonzero_otherwise_weighted_average_fill_price"
                if used_persisted_pnl
                else "weighted_average_fill_price_from_persisted_trades"
            ),
            "closed_outcome_count": closed,
            "winning_closed_fills": wins,
            "losing_closed_fills": losses,
            "breakeven_closed_fills": breakevens,
            "unmatched_sell_fills": unmatched_sells,
            "truthful_win_rate": (wins / closed) if closed else 0.0,
            "fill_derived_closed_pnl": round(sum(outcomes), 8),
        }

    except Exception as exc:
        result = {
            "metrics_available": False,
            "metrics_basis": "unavailable",
            "metrics_error": repr(exc),
            "closed_outcome_count": None,
            "winning_closed_fills": None,
            "losing_closed_fills": None,
            "breakeven_closed_fills": None,
            "unmatched_sell_fills": None,
            "truthful_win_rate": None,
            "fill_derived_closed_pnl": None,
        }

    _qfos_truth_metrics_v3_cache["at"] = now
    _qfos_truth_metrics_v3_cache["value"] = result
    return result


@app.middleware("http")
async def _qfos_active_truth_metrics_v3_middleware(request, call_next):
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
        performance = payload.get("performance")

        if not isinstance(performance, dict):
            performance = {}
            payload["performance"] = performance

        truth = _qfos_active_truth_metrics_v3()

        performance["metrics_available"] = truth["metrics_available"]
        performance["metrics_basis"] = truth["metrics_basis"]
        performance["metrics_error"] = truth.get("metrics_error")
        performance["closed_outcome_count"] = truth["closed_outcome_count"]
        performance["winning_closed_fills"] = truth["winning_closed_fills"]
        performance["losing_closed_fills"] = truth["losing_closed_fills"]
        performance["breakeven_closed_fills"] = truth["breakeven_closed_fills"]
        performance["unmatched_sell_fills"] = truth["unmatched_sell_fills"]
        performance["fill_derived_closed_pnl"] = truth["fill_derived_closed_pnl"]
        performance["gross_fill_price_realized_pnl"] = truth["fill_derived_closed_pnl"]

        if truth["metrics_available"]:
            rate = round(float(truth["truthful_win_rate"]), 4)
            performance["win_rate"] = rate
            performance["win_rate_estimate"] = rate
        else:
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
            f"[QFOS_ACTIVE_TRUTH_METRICS_ERROR] error={exc!r}",
            flush=True,
        )
        return response
'''

    api_path.write_text(api + patch, encoding="utf-8")
    print("ACTIVE_API_PATCH_OK")
else:
    print("ACTIVE_API_PATCH_ALREADY_PRESENT")
