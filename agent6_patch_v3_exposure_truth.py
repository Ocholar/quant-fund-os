from pathlib import Path
import re

path = Path("services/api.py")
src = path.read_text(encoding="utf-8")

helper = r'''

# ============================================================
# AGENT6_EXPOSURE_TRUTH_ROUTE_PATCH_V3
#
# Direct route-level API truth fix.
# Do not trust stale portfolio_snapshots.exposure for /status
# when live open positions are present.
# ============================================================

def _agent6_v3_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _agent6_v3_status_positions_exposure(payload):
    total = 0.0
    positions = payload.get("positions") if isinstance(payload, dict) else []
    if not isinstance(positions, list):
        return 0.0

    for pos in positions:
        if isinstance(pos, dict):
            total += _agent6_v3_float(pos.get("exposure"), 0.0)

    # Match dashboard-visible rounded position exposures.
    return round(total, 2)


def _agent6_v3_postgres_positions_exposure():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    COALESCE(SUM(exposure), 0) AS exposure_sum,
                    COUNT(*) AS open_positions
                FROM positions
                WHERE quantity > 0.00000001
                  AND exposure >= 0.05
            """)).mappings().first()

        return (
            round(_agent6_v3_float(row.get("exposure_sum") if row else 0.0), 2),
            int(row.get("open_positions") or 0) if row else 0,
        )
    except Exception as exc:
        try:
            print("[AGENT6_EXPOSURE_TRUTH_V3] postgres exposure sum failed:", exc)
        except Exception:
            pass
        return 0.0, 0


def _agent6_v3_apply_status_exposure_truth(payload):
    if not isinstance(payload, dict):
        return payload

    portfolio = payload.get("portfolio")
    if not isinstance(portfolio, dict):
        return payload

    positions = payload.get("positions")
    if not isinstance(positions, list):
        positions = []

    warnings = payload.get("anomaly_warnings")
    if not isinstance(warnings, list):
        warnings = []

    raw_exposure = _agent6_v3_float(portfolio.get("exposure"), 0.0)
    corrected_exposure = _agent6_v3_status_positions_exposure(payload)
    equity = _agent6_v3_float(portfolio.get("equity"), 0.0)

    if len(positions) > 0:
        if abs(raw_exposure - corrected_exposure) > 0.01:
            warnings.append({
                "type": "exposure_position_mismatch",
                "severity": "warning",
                "message": "Raw portfolio exposure did not match summed open-position exposure; /status portfolio.exposure was corrected from live positions.",
                "portfolio_exposure_raw": round(raw_exposure, 8),
                "positions_exposure_sum": round(corrected_exposure, 8),
                "delta": round(raw_exposure - corrected_exposure, 8),
                "source": "agent6_exposure_truth_route_patch_v3",
            })

        portfolio["snapshot_exposure"] = round(raw_exposure, 8)
        portfolio["exposure"] = corrected_exposure
        portfolio["exposure_pct"] = round(corrected_exposure / equity, 6) if equity > 0 else 0.0
        portfolio["exposure_source"] = "sum_status_positions"
    else:
        if abs(raw_exposure) > 0.01:
            warnings.append({
                "type": "exposure_without_positions",
                "severity": "critical",
                "message": "Raw portfolio exposure was non-zero while no open positions existed; /status exposure forced to zero.",
                "portfolio_exposure_raw": round(raw_exposure, 8),
                "source": "agent6_exposure_truth_route_patch_v3",
            })

        portfolio["snapshot_exposure"] = round(raw_exposure, 8)
        portfolio["exposure"] = 0.0
        portfolio["exposure_pct"] = 0.0
        portfolio["exposure_source"] = "no_open_positions"

    payload["portfolio"] = portfolio
    payload["anomaly_warnings"] = warnings
    return payload

# ============================================================
# END AGENT6_EXPOSURE_TRUTH_ROUTE_PATCH_V3
# ============================================================

'''

if "AGENT6_EXPOSURE_TRUTH_ROUTE_PATCH_V3" not in src:
    marker = '@app.get("/status")'
    if marker not in src:
        raise SystemExit('Could not find @app.get("/status") marker.')
    src = src.replace(marker, helper + "\n" + marker, 1)

new_status = r'''@app.get("/status")
def status():
    payload = get_status_payload()
    control = get_control_state()
    paused = control["paused"]
    reason = control.get("reason") or ""

    trades = payload.get("trading", {}).get("total_trades", 0)
    if trades == 0 and "max_daily_loss" in reason:
        paused = False
        reason = ""
        resume_bot()

    payload["paused"] = paused
    payload["pause_reason"] = reason
    payload["bot_state"] = "PAUSED" if paused else "RUNNING"
    payload["status_label"] = payload["bot_state"]

    if trades == 0:
        payload["risk_status"] = "SAFE"

    payload["controls"] = {
        "pause": "/pause",
        "resume": "/resume",
        "kill_switch": "/kill-switch",
    }

    # Agent 6 v3: correct portfolio exposure from live displayed positions before anomaly checks.
    payload = _agent6_v3_apply_status_exposure_truth(payload)

    existing_warnings = payload.get("anomaly_warnings")
    if not isinstance(existing_warnings, list):
        existing_warnings = []

    payload["anomaly_warnings"] = existing_warnings + _compute_anomaly_warnings(payload)

    return qfos_normalize_payload(payload)
'''

src, n_status = re.subn(
    r'@app\.get\("/status"\)\s*def status\(\):.*?(?=\n@app\.post\("/pause"\))',
    new_status,
    src,
    count=1,
    flags=re.S,
)

if n_status != 1:
    raise SystemExit(f"Expected to replace exactly one /status route, replaced {n_status}.")

new_portfolio_latest = r'''@app.get("/portfolio/latest")
def latest_portfolio():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT equity, cash, exposure, drawdown, regime, created_at
                FROM portfolio_snapshots
                ORDER BY id DESC
                LIMIT 1
            """)).mappings().first()

            pos_row = conn.execute(text("""
                SELECT
                    COALESCE(SUM(exposure), 0) AS positions_exposure_sum,
                    COUNT(*) AS open_positions
                FROM positions
                WHERE quantity > 0.00000001
                  AND exposure >= 0.05
            """)).mappings().first()

        if row:
            out = {}
            for k, v in dict(row).items():
                out[k] = v.isoformat() if hasattr(v, "isoformat") else v
        else:
            out = {
                "equity": 100,
                "cash": 100,
                "exposure": 0,
                "drawdown": 0,
                "regime": "UNKNOWN",
            }

        raw_exposure = _agent6_v3_float(out.get("exposure"), 0.0)
        positions_exposure_sum = round(_agent6_v3_float(pos_row.get("positions_exposure_sum") if pos_row else 0.0), 2)
        open_positions = int(pos_row.get("open_positions") or 0) if pos_row else 0
        equity = _agent6_v3_float(out.get("equity"), 0.0)

        out["snapshot_exposure"] = round(raw_exposure, 8)
        out["open_positions"] = open_positions

        if open_positions > 0:
            out["exposure"] = positions_exposure_sum
            out["exposure_source"] = "sum_postgres_positions"
            out["exposure_mismatch_delta"] = round(raw_exposure - positions_exposure_sum, 8)
        else:
            out["exposure"] = 0.0
            out["exposure_source"] = "no_open_positions"
            out["exposure_mismatch_delta"] = round(raw_exposure, 8)

        out["exposure_pct"] = round(_agent6_v3_float(out.get("exposure"), 0.0) / equity, 6) if equity > 0 else 0.0

        return out

    except Exception as e:
        print("API /portfolio/latest ERROR:", e)

    return {
        "equity": 100,
        "cash": 100,
        "exposure": 0,
        "drawdown": 0,
        "regime": "UNKNOWN",
        "snapshot_exposure": 0,
        "open_positions": 0,
        "exposure_source": "fallback",
        "exposure_pct": 0,
    }
'''

# Replace every /portfolio/latest route definition, because duplicate FastAPI routes can leave an older one active.
src, n_portfolio = re.subn(
    r'@app\.get\("/portfolio/latest"\)\s*def latest_portfolio\(\):.*?(?=\n@app\.)',
    new_portfolio_latest + "\n\n",
    src,
    flags=re.S,
)

if n_portfolio < 1:
    raise SystemExit("Could not find /portfolio/latest route to replace.")

path.write_text(src, encoding="utf-8")

print(f"PATCH_OK status_routes_replaced={n_status} portfolio_latest_routes_replaced={n_portfolio}")
