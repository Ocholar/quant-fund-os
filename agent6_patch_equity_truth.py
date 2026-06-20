from pathlib import Path
import re

path = Path("services/api.py")
src = path.read_text(encoding="utf-8")

patch = r'''
# ============================================================
# AGENT6_EQUITY_TRUTH_PATCH_V1
#
# Purpose:
#   API-facing portfolio truth must satisfy:
#       equity = cash + exposure
#
# Scope:
#   /status and /portfolio/latest serialization only.
#   Does not touch execution, risk, allocation, feature generation,
#   cash/equity authority, ledger writes, or live trading.
# ============================================================

def _agent6_equity_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _agent6_equity_round_money(value):
    try:
        return round(float(value), 2)
    except Exception:
        return 0.0


def _agent6_equity_patch_status_payload(payload):
    if not isinstance(payload, dict):
        return payload

    portfolio = payload.get("portfolio")
    if not isinstance(portfolio, dict):
        return payload

    cash = _agent6_equity_float(portfolio.get("cash"), 0.0)
    exposure = _agent6_equity_float(portfolio.get("exposure"), 0.0)

    corrected_equity = cash + exposure

    portfolio["equity"] = _agent6_equity_round_money(corrected_equity)
    portfolio["equity_source"] = "cash_plus_exposure"
    portfolio["equity_minus_cash_exposure"] = round(
        _agent6_equity_float(portfolio.get("equity"), 0.0) - (cash + exposure),
        6,
    )

    try:
        portfolio["exposure_pct"] = round(exposure / corrected_equity, 6) if corrected_equity > 0 else 0.0
    except Exception:
        portfolio["exposure_pct"] = 0.0

    payload["portfolio"] = portfolio
    return payload


def _agent6_equity_patch_portfolio_latest_payload(payload):
    if not isinstance(payload, dict):
        return payload

    cash = _agent6_equity_float(payload.get("cash"), 0.0)
    exposure = _agent6_equity_float(payload.get("exposure"), 0.0)

    corrected_equity = cash + exposure

    payload["equity"] = round(corrected_equity, 8)
    payload["equity_source"] = "cash_plus_exposure"
    payload["equity_minus_cash_exposure"] = round(
        _agent6_equity_float(payload.get("equity"), 0.0) - (cash + exposure),
        8,
    )

    try:
        payload["exposure_pct"] = round(exposure / corrected_equity, 6) if corrected_equity > 0 else 0.0
    except Exception:
        payload["exposure_pct"] = 0.0

    return payload


@app.middleware("http")
async def agent6_equity_truth_middleware_v1(request, call_next):
    response = await call_next(request)
    path = str(request.url.path)

    if path not in {"/status", "/portfolio/latest"}:
        return response

    if "application/json" not in response.headers.get("content-type", ""):
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        import json
        from fastapi.responses import JSONResponse

        payload = json.loads(body.decode("utf-8"))

        # Some legacy normalizers may double-encode JSON.
        if isinstance(payload, str):
            payload = json.loads(payload)

        if path == "/status":
            payload = _agent6_equity_patch_status_payload(payload)
        elif path == "/portfolio/latest":
            payload = _agent6_equity_patch_portfolio_latest_payload(payload)

        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in {"content-length", "content-type"}
        }

        return JSONResponse(
            content=payload,
            status_code=response.status_code,
            headers=headers,
        )

    except Exception as exc:
        try:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content={
                    "error": "agent6_equity_truth_patch_failed",
                    "detail": str(exc),
                },
                status_code=500,
            )
        except Exception:
            raise

# ============================================================
# END AGENT6_EQUITY_TRUTH_PATCH_V1
# ============================================================
'''

if "AGENT6_EQUITY_TRUTH_PATCH_V1" in src:
    print("PATCH_ALREADY_PRESENT")
else:
    # Insert after the existing Agent 6 exposure truth middleware if present,
    # otherwise append near the end. Middleware order in FastAPI is acceptable
    # here because this patch is a final response serialization safety layer.
    marker = "# END AGENT6_EXPOSURE_TRUTH_FINAL_MIDDLEWARE_V2"
    if marker in src:
        idx = src.find(marker)
        endline = src.find("\n", idx)
        if endline == -1:
            endline = len(src)
        src = src[:endline+1] + "\n\n" + patch + "\n\n" + src[endline+1:]
    else:
        src = src.rstrip() + "\n\n" + patch + "\n"

path.write_text(src, encoding="utf-8")
print("PATCH_OK marker_present=", "AGENT6_EQUITY_TRUTH_PATCH_V1" in src)
