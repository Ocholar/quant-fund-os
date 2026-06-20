from pathlib import Path
import io
import re
import tokenize

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_TRUTHFUL_STATUS_METRICS_MIDDLEWARE_V1"
if marker in src:
    print("STATUS_METRICS_PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

def find_fastapi_assignment_end(text: str) -> int:
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for idx in range(len(tokens) - 3):
        a, b, c, d = tokens[idx:idx + 4]
        if (
            a.type == tokenize.NAME and a.string == "app"
            and b.type == tokenize.OP and b.string == "="
            and c.type == tokenize.NAME and c.string == "FastAPI"
            and d.type == tokenize.OP and d.string == "("
        ):
            depth = 0
            for tok in tokens[idx + 3:]:
                if tok.type == tokenize.OP and tok.string == "(":
                    depth += 1
                elif tok.type == tokenize.OP and tok.string == ")":
                    depth -= 1
                    if depth == 0:
                        lines = text.splitlines(keepends=True)
                        absolute = sum(len(line) for line in lines[:tok.end[0] - 1]) + tok.end[1]
                        return absolute
    raise RuntimeError("PATCH_FAILED: app = FastAPI(...) assignment not found")

insert_at = find_fastapi_assignment_end(src)

patch = r'''

# QFOS_TRUTHFUL_STATUS_METRICS_MIDDLEWARE_V1
# This is presentation/accounting telemetry only. It never changes orders,
# positions, exits, portfolio cash, realized PnL, or strategy decisions.
_qfos_truth_metrics_cache = {"at": 0.0, "value": None}

def _qfos_truthful_closed_fill_metrics():
    import os
    import time

    now = time.monotonic()
    cached = _qfos_truth_metrics_cache.get("value")
    if cached is not None and (now - _qfos_truth_metrics_cache.get("at", 0.0)) < 2.0:
        return cached

    dsn_candidates = [
        os.getenv("DATABASE_URL"),
        os.getenv("POSTGRES_DSN"),
        os.getenv("DB_URL"),
        (
            "postgresql://"
            f"{os.getenv('POSTGRES_USER', 'qfos')}:"
            f"{os.getenv('POSTGRES_PASSWORD', 'qfos')}@"
            f"{os.getenv('POSTGRES_HOST', 'postgres')}:"
            f"{os.getenv('POSTGRES_PORT', '5432')}/"
            f"{os.getenv('POSTGRES_DB', 'quant_fund_os')}"
        ),
    ]
    dsn_candidates = [item for item in dsn_candidates if item]

    rows = None
    last_error = None

    for dsn in dsn_candidates:
        for driver_name in ("psycopg", "psycopg2"):
            try:
                if driver_name == "psycopg":
                    import psycopg
                    with psycopg.connect(dsn, connect_timeout=3) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    id,
                                    symbol,
                                    LOWER(COALESCE(side, '')) AS side,
                                    COALESCE(quantity, 0) AS quantity,
                                    COALESCE(fill_price, expected_price, 0) AS price
                                FROM trades
                                ORDER BY id ASC
                                """
                            )
                            rows = cur.fetchall()
                else:
                    import psycopg2
                    with psycopg2.connect(dsn, connect_timeout=3) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT
                                    id,
                                    symbol,
                                    LOWER(COALESCE(side, '')) AS side,
                                    COALESCE(quantity, 0) AS quantity,
                                    COALESCE(fill_price, expected_price, 0) AS price
                                FROM trades
                                ORDER BY id ASC
                                """
                            )
                            rows = cur.fetchall()

                if rows is not None:
                    break
            except Exception as exc:
                last_error = repr(exc)

        if rows is not None:
            break

    if rows is None:
        return {
            "metrics_available": False,
            "metrics_error": last_error or "database_metrics_unavailable",
        }

    state = {}
    wins = 0
    losses = 0
    breakevens = 0
    closed_sell_fills = 0
    gross_realized = 0.0
    eps = 1e-10

    for _, symbol, side, quantity, price in rows:
        try:
            qty = abs(float(quantity or 0.0))
            px = float(price or 0.0)
        except Exception:
            continue

        if not symbol or qty <= eps or px <= 0:
            continue

        current_qty, avg_entry = state.get(symbol, (0.0, 0.0))

        if side == "buy":
            next_qty = current_qty + qty
            next_avg = (
                ((current_qty * avg_entry) + (qty * px)) / next_qty
                if next_qty > eps else 0.0
            )
            state[symbol] = (next_qty, next_avg)
            continue

        if side != "sell" or current_qty <= eps:
            continue

        close_qty = min(qty, current_qty)
        close_pnl = (px - avg_entry) * close_qty

        closed_sell_fills += 1
        gross_realized += close_pnl

        if close_pnl > eps:
            wins += 1
        elif close_pnl < -eps:
            losses += 1
        else:
            breakevens += 1

        remaining = current_qty - close_qty
        state[symbol] = (remaining, avg_entry) if remaining > eps else (0.0, 0.0)

    denominator = wins + losses + breakevens
    result = {
        "metrics_available": True,
        "metrics_basis": "weighted_average_fill_price_before_fees",
        "closed_sell_fills": closed_sell_fills,
        "winning_closed_fills": wins,
        "losing_closed_fills": losses,
        "breakeven_closed_fills": breakevens,
        "truthful_win_rate": (wins / denominator) if denominator else 0.0,
        "gross_fill_price_realized_pnl": gross_realized,
    }

    _qfos_truth_metrics_cache["at"] = now
    _qfos_truth_metrics_cache["value"] = result
    return result


@app.middleware("http")
async def _qfos_truthful_status_metrics_middleware(request, call_next):
    response = await call_next(request)

    if request.url.path != "/status":
        return response

    try:
        import json
        from fastapi.responses import Response

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        payload = json.loads(body.decode("utf-8"))
        performance = payload.get("performance")

        if isinstance(performance, dict):
            truth = _qfos_truthful_closed_fill_metrics()

            if truth.get("metrics_available"):
                performance["win_rate"] = truth["truthful_win_rate"]
                performance["win_rate_estimate"] = truth["truthful_win_rate"]
                performance["closed_outcome_count"] = truth["closed_sell_fills"]
                performance["winning_closed_fills"] = truth["winning_closed_fills"]
                performance["losing_closed_fills"] = truth["losing_closed_fills"]
                performance["breakeven_closed_fills"] = truth["breakeven_closed_fills"]
                performance["metrics_basis"] = truth["metrics_basis"]
                performance["gross_fill_price_realized_pnl"] = truth[
                    "gross_fill_price_realized_pnl"
                ]
            else:
                performance["metrics_basis"] = "engine_metric_fallback"
                performance["metrics_error"] = truth.get("metrics_error")

        headers = dict(response.headers)
        headers.pop("content-length", None)

        return Response(
            content=json.dumps(payload, default=str).encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=response.background,
        )
    except Exception as exc:
        print(f"[QFOS_STATUS_METRICS_ERROR] error={exc!r}", flush=True)
        return response
'''

src = src[:insert_at] + patch + src[insert_at:]
path.write_text(src, encoding="utf-8")
print("STATUS_METRICS_PATCH_WRITE_OK")
