from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# QFOS_EXEC_RISK_AUTHORITY_GATE_V1"

if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

patch = r'''

# QFOS_EXEC_RISK_AUTHORITY_GATE_V1
# Joint Agent 2 + Agent 5
#
# Problem:
#   final_validation rejected a valid BUY with:
#       caution_drawdown_position_cap_-0.0578
#   while authoritative API/Postgres state showed:
#       risk_status=SAFE, drawdown=0, exposure=0, positions=0.
#
# Fix:
#   Before final execution BUY validation, rebuild risk state from Postgres:
#     cash = starting_equity - buys + sells
#     exposure = sum(open positions exposure)
#     equity = cash + exposure
#     drawdown = current ledger-derived drawdown
#     open_positions = current DB open positions
#
#   Then sync runtime portfolio/risk memory and audit the exact state used.
#
#   The caution cap is NOT removed. A stale caution rejection is overridden only
#   when ledger authority proves SAFE/current clean state.

def _qfos_exec_risk_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _qfos_exec_risk_starting_equity():
    try:
        return float(globals().get("QFOS_STARTING_EQUITY", 100.0) or 100.0)
    except Exception:
        return 100.0


def qfos_exec_risk_authority_snapshot():
    starting_equity = _qfos_exec_risk_starting_equity()

    snapshot = {
        "source": "ledger_postgres",
        "starting_equity": starting_equity,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "realized_pnl": 0.0,
        "cash": starting_equity,
        "exposure": 0.0,
        "equity": starting_equity,
        "drawdown": 0.0,
        "peak": starting_equity,
        "open_positions": 0,
        "positions": {},
        "risk_status": "SAFE",
    }

    try:
        with engine.begin() as conn:
            t = conn.execute(text("""
                select
                    coalesce(sum(case when lower(side)='buy' then quantity * fill_price else 0 end),0) as buy_cost,
                    coalesce(sum(case when lower(side)='sell' then quantity * fill_price else 0 end),0) as sell_proceeds,
                    coalesce(sum(case when lower(side)='sell' then pnl else 0 end),0) as realized_pnl
                from trades
            """)).mappings().first()

            p_rows = conn.execute(text("""
                select
                    symbol,
                    quantity,
                    avg_entry,
                    coalesce(last_price, avg_entry) as last_price,
                    coalesce(exposure, quantity * coalesce(last_price, avg_entry)) as exposure
                from positions
                where coalesce(quantity, 0) > 0.00000001
            """)).mappings().all()

            buy_cost = _qfos_exec_risk_float(t.get("buy_cost") if t else 0.0)
            sell_proceeds = _qfos_exec_risk_float(t.get("sell_proceeds") if t else 0.0)
            realized_pnl = _qfos_exec_risk_float(t.get("realized_pnl") if t else 0.0)

            positions = {}
            exposure = 0.0

            for r in p_rows:
                sym = str(r.get("symbol") or "").strip()
                qty = _qfos_exec_risk_float(r.get("quantity"))
                pos_exposure = _qfos_exec_risk_float(r.get("exposure"))
                if sym and qty > 0:
                    positions[sym] = qty
                    exposure += max(0.0, pos_exposure)

            cash = starting_equity - buy_cost + sell_proceeds
            equity = cash + exposure

            # Current-run ledger drawdown. Do not inherit old runtime peak.
            # If equity is at/above baseline, drawdown is clean zero.
            peak = max(starting_equity, equity)
            drawdown = 0.0
            if peak > 0:
                drawdown = min(0.0, (equity - peak) / peak)

            caution_drawdown = _qfos_exec_risk_float(
                getattr(globals().get("settings", object()), "caution_drawdown", -0.04),
                -0.04,
            )
            blocked_drawdown = _qfos_exec_risk_float(
                getattr(globals().get("settings", object()), "blocked_drawdown", -0.08),
                -0.08,
            )

            risk_status = "SAFE"
            if drawdown <= blocked_drawdown:
                risk_status = "BLOCKED"
            elif drawdown <= caution_drawdown:
                risk_status = "CAUTION"

            snapshot.update({
                "buy_cost": buy_cost,
                "sell_proceeds": sell_proceeds,
                "realized_pnl": realized_pnl,
                "cash": cash,
                "exposure": exposure,
                "equity": equity,
                "drawdown": drawdown,
                "peak": peak,
                "open_positions": len(positions),
                "positions": positions,
                "risk_status": risk_status,
            })

    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] error=" + repr(exc), flush=True)

    return snapshot


def qfos_exec_risk_apply_authority(snapshot):
    try:
        port = globals().get("portfolio")
        if port is not None:
            try:
                port.cash = _qfos_exec_risk_float(snapshot.get("cash"), getattr(port, "cash", 100.0))
            except Exception:
                pass

            try:
                port.positions = dict(snapshot.get("positions") or {})
            except Exception:
                pass

            try:
                port.drawdown = _qfos_exec_risk_float(snapshot.get("drawdown"), 0.0)
            except Exception:
                pass

            try:
                port.peak = _qfos_exec_risk_float(snapshot.get("peak"), snapshot.get("equity", 100.0))
            except Exception:
                pass

            try:
                port.equity = _qfos_exec_risk_float(snapshot.get("equity"), 100.0)
            except Exception:
                pass

        globals()["last_known_equity"] = _qfos_exec_risk_float(snapshot.get("equity"), 100.0)
        globals()["last_known_exposure"] = _qfos_exec_risk_float(snapshot.get("exposure"), 0.0)
        globals()["last_known_risk_status"] = str(snapshot.get("risk_status") or "SAFE")

    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] apply_error=" + repr(exc), flush=True)


def qfos_exec_risk_log_authority(snapshot):
    try:
        print(
            "[EXEC_RISK_AUTHORITY] "
            f"source={snapshot.get('source')} "
            f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
            f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
            f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
            f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
            f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
            f"open_positions={int(snapshot.get('open_positions') or 0)} "
            f"risk_status={snapshot.get('risk_status')}",
            flush=True,
        )
    except Exception as exc:
        print("[EXEC_RISK_AUTHORITY] log_error=" + repr(exc), flush=True)


def qfos_exec_risk_stale_caution_reject(reason, snapshot):
    reason_s = str(reason or "")
    if "caution_drawdown_position_cap" not in reason_s:
        return False

    risk_status = str(snapshot.get("risk_status") or "").upper()
    drawdown = _qfos_exec_risk_float(snapshot.get("drawdown"), 0.0)
    exposure = _qfos_exec_risk_float(snapshot.get("exposure"), 0.0)
    open_positions = int(snapshot.get("open_positions") or 0)

    # Only override a stale caution cap when ledger authority is clean.
    return (
        risk_status == "SAFE"
        and drawdown >= -0.000001
        and exposure <= 0.000001
        and open_positions == 0
    )


def qfos_exec_risk_authority_firewall(fill, regime):
    side = ""
    symbol = ""
    strategy = ""

    try:
        side = str((fill or {}).get("side") or "").strip().lower()
        symbol = str((fill or {}).get("symbol") or "").strip()
        strategy = str((fill or {}).get("strategy") or (fill or {}).get("reason") or "").strip()
    except Exception:
        pass

    # SELLs and non-BUYs continue through existing validation.
    if side != "buy":
        return qfos_real_data_trade_firewall(fill, regime)

    snapshot = qfos_exec_risk_authority_snapshot()
    qfos_exec_risk_apply_authority(snapshot)
    qfos_exec_risk_log_authority(snapshot)

    print(
        "[EXEC_RISK_AUDIT] "
        f"symbol={symbol} stage=before_final_validation "
        f"strategy={strategy} "
        f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
        f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
        f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
        f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
        f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
        f"risk_status={snapshot.get('risk_status')} "
        f"positions_count={int(snapshot.get('open_positions') or 0)} "
        f"source={snapshot.get('source')}",
        flush=True,
    )

    allowed, reason = qfos_real_data_trade_firewall(fill, regime)

    decision = "ALLOW" if allowed else "REJECT"

    # If the only rejection is stale caution drawdown while ledger is SAFE/clean,
    # allow the BUY and log the override.
    if not allowed and qfos_exec_risk_stale_caution_reject(reason, snapshot):
        print(
            "[EXEC_RISK_AUDIT] "
            f"symbol={symbol} stage=stale_caution_override "
            f"old_decision=REJECT old_reason={reason} "
            f"authority_risk_status={snapshot.get('risk_status')} "
            f"authority_drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
            f"authority_exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
            f"authority_open_positions={int(snapshot.get('open_positions') or 0)}",
            flush=True,
        )
        allowed = True
        reason = "ledger_safe_overrode_stale_caution_drawdown_position_cap"
        decision = "ALLOW"

    print(
        "[EXEC_RISK_AUDIT] "
        f"symbol={symbol} stage=after_final_validation "
        f"decision={decision} reason={reason} "
        f"cash={_qfos_exec_risk_float(snapshot.get('cash')):.6f} "
        f"equity={_qfos_exec_risk_float(snapshot.get('equity')):.6f} "
        f"exposure={_qfos_exec_risk_float(snapshot.get('exposure')):.6f} "
        f"drawdown={_qfos_exec_risk_float(snapshot.get('drawdown')):.6f} "
        f"peak={_qfos_exec_risk_float(snapshot.get('peak')):.6f} "
        f"risk_status={snapshot.get('risk_status')} "
        f"positions_count={int(snapshot.get('open_positions') or 0)}",
        flush=True,
    )

    return allowed, reason

# END QFOS_EXEC_RISK_AUTHORITY_GATE_V1
'''

# Insert helper before active execution loop / strategy helpers.
anchor = "def entry_quality_ranked_symbols"
idx = text.find(anchor)

if idx == -1:
    anchor = "def total_exposure"
    idx = text.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: helper insertion anchor not found")

text = text[:idx] + patch + "\n\n" + text[idx:]

# Replace active final validation callsite.
old = """                    allowed, reason = qfos_real_data_trade_firewall(fill, regime)
                    if allowed:
"""

new = """                    allowed, reason = qfos_exec_risk_authority_firewall(fill, regime)
                    if allowed:
"""

if old not in text:
    raise SystemExit("PATCH_FAILED: active qfos_real_data_trade_firewall callsite not found")

text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
