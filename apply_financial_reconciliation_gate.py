from pathlib import Path

path = Path("services/api.py")
text = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_FINANCIAL_RECONCILIATION_GATE_V1"

if marker in text:
    print("RECONCILIATION_GATE_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_FINANCIAL_RECONCILIATION_GATE_V1
# Final presentation-only safety gate. It does not modify trades, portfolio,
# balances, strategy decisions, or execution. It prevents an unreconciled
# historical metric from being shown as financially validated.

@app.middleware("http")
async def _qfos_financial_reconciliation_gate_v1(request, call_next):
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

        try:
            ledger_realized = float(portfolio.get("realized_pnl") or 0.0)
            fill_derived = float(performance.get("fill_derived_closed_pnl") or 0.0)
            delta = round(fill_derived - ledger_realized, 8)
            available = bool(performance.get("metrics_available"))
        except Exception as exc:
            ledger_realized = None
            fill_derived = None
            delta = None
            available = False
            performance["metrics_error"] = (
                f"reconciliation_parse_error:{exc!r}"
            )

        tolerance = 0.01
        blocked = (
            delta is None
            or abs(delta) > tolerance
            or not available
        )

        performance["ledger_realized_pnl"] = ledger_realized
        performance["metrics_reconciliation_delta"] = delta
        performance["metrics_reconciliation_tolerance"] = tolerance
        performance["metrics_reconciliation_gate"] = (
            "BLOCKED" if blocked else "PASS"
        )

        if blocked:
            performance["metrics_available"] = False
            performance["metrics_error"] = (
                "financial_reconciliation_mismatch: "
                f"ledger_realized={ledger_realized}; "
                f"fill_derived={fill_derived}; "
                f"delta={delta}; tolerance={tolerance}"
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
            f"[QFOS_FINANCIAL_RECONCILIATION_GATE_ERROR] error={exc!r}",
            flush=True,
        )
        return response
'''

path.write_text(text + patch, encoding="utf-8")
print("RECONCILIATION_GATE_PATCH_OK")