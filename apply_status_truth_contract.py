from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_STATUS_TRUTH_CONTRACT_FINAL_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

anchor = "@app.get('/live-status')"
if anchor not in src:
    anchor = '@app.get("/live-status")'

if anchor not in src:
    raise SystemExit(
        "PATCH_FAILED: live-status route anchor not found in main.py. "
        "No source was changed."
    )

patch = r'''

# QFOS_STATUS_TRUTH_CONTRACT_FINAL_V1
# Final response contract for /status. Presentation only: it never changes
# strategy, orders, position accounting, cash, or realized PnL.
@app.middleware("http")
async def _qfos_status_truth_contract_final(request, call_next):
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

        if not isinstance(performance, dict):
            return Response(
                content=json.dumps(payload, default=str).encode("utf-8"),
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
                media_type="application/json",
                background=response.background,
            )

        truth = _qfos_truthful_closed_fill_metrics()

        if truth.get("metrics_available"):
            closed = int(
                truth.get("closed_sell_fills", truth.get("closed_outcome_count", 0)) or 0
            )
            wins = int(truth.get("winning_closed_fills", 0) or 0)
            losses = int(truth.get("losing_closed_fills", 0) or 0)
            breakevens = int(truth.get("breakeven_closed_fills", 0) or 0)

            gross_pnl = float(
                truth.get(
                    "gross_fill_price_realized_pnl",
                    truth.get("fill_derived_closed_pnl", 0.0),
                ) or 0.0
            )

            rate = float(truth.get("truthful_win_rate", 0.0) or 0.0)

            performance["win_rate"] = round(rate, 4)
            performance["win_rate_estimate"] = round(rate, 4)
            performance["closed_outcome_count"] = closed
            performance["winning_closed_fills"] = wins
            performance["losing_closed_fills"] = losses
            performance["breakeven_closed_fills"] = breakevens

            # The active helper does not independently calculate unmatched
            # sells. Expose that limitation explicitly instead of inventing 0.
            performance["unmatched_sell_fills"] = truth.get("unmatched_sell_fills")
            performance["metrics_basis"] = truth.get(
                "metrics_basis",
                "weighted_average_fill_price_before_fees",
            )
            performance["gross_fill_price_realized_pnl"] = gross_pnl
            performance["fill_derived_closed_pnl"] = gross_pnl
            performance["metrics_available"] = True
            performance["metrics_error"] = None
        else:
            performance["metrics_available"] = False
            performance["metrics_basis"] = truth.get("metrics_basis", "unavailable")
            performance["metrics_error"] = truth.get("metrics_error", "unknown")
            performance["winning_closed_fills"] = None
            performance["losing_closed_fills"] = None
            performance["breakeven_closed_fills"] = None
            performance["unmatched_sell_fills"] = None
            performance["gross_fill_price_realized_pnl"] = None
            performance["fill_derived_closed_pnl"] = None

        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() != "content-length"
        }

        return Response(
            content=json.dumps(payload, default=str).encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
            background=response.background,
        )

    except Exception as exc:
        print(f"[QFOS_STATUS_TRUTH_CONTRACT_ERROR] error={exc!r}", flush=True)
        return response
'''

src = src.replace(anchor, patch + "\n" + anchor, 1)
path.write_text(src, encoding="utf-8")

print("PATCH_WRITE_OK")
