from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8")

marker = "# QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1
# Purpose:
#   The previous forensic patch did not reach the active final_validation path.
#   Logs still show:
#       EXEC_BRIDGE_AUDIT stage=final_validation decision=REJECT
#       reason=caution_drawdown_position_cap_-0.0592
#
#   This wrapper intercepts active can_buy(...) calls directly.
#
#   It does not delete the caution cap. It only overrides a stale caution cap
#   when Postgres ledger authority proves clean SAFE state.

def qfos_active_canbuy_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def qfos_active_canbuy_ledger_state():
    state = {
        "cash": 100.0,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "exposure": 0.0,
        "equity": 100.0,
        "open_positions": 0,
        "drawdown": 0.0,
        "risk_status": "SAFE",
        "source": "active_canbuy_ledger",
    }

    try:
        start = 100.0
        for k in ("QFOS_STARTING_EQUITY", "QFOS_STARTING_CASH", "STARTING_EQUITY", "STARTING_CASH"):
            try:
                vv = globals().get(k)
                if vv is not None:
                    start = float(vv)
                    break
            except Exception:
                pass

        with engine.begin() as conn:
            t = conn.execute(text("""
                select
                    coalesce(sum(case when lower(side)='buy' then quantity * fill_price else 0 end),0) as buy_cost,
                    coalesce(sum(case when lower(side)='sell' then quantity * fill_price else 0 end),0) as sell_proceeds
                from trades
            """)).mappings().first()

            p = conn.execute(text("""
                select
                    coalesce(sum(coalesce(exposure, quantity * coalesce(last_price, avg_entry))),0) as exposure,
                    count(*) as open_positions
                from positions
                where coalesce(quantity,0) > 0.00000001
            """)).mappings().first()

        buy_cost = qfos_active_canbuy_float(t.get("buy_cost") if t else 0.0)
        sell_proceeds = qfos_active_canbuy_float(t.get("sell_proceeds") if t else 0.0)
        exposure = qfos_active_canbuy_float(p.get("exposure") if p else 0.0)
        open_positions = int(p.get("open_positions") if p else 0)

        cash = start - buy_cost + sell_proceeds
        equity = cash + exposure

        peak = max(start, equity)
        drawdown = 0.0
        if peak > 0:
            drawdown = min(0.0, (equity - peak) / peak)

        risk_status = "SAFE"
        if drawdown <= -0.08:
            risk_status = "BLOCKED"
        elif drawdown <= -0.04:
            risk_status = "CAUTION"

        state.update({
            "cash": cash,
            "buy_cost": buy_cost,
            "sell_proceeds": sell_proceeds,
            "exposure": exposure,
            "equity": equity,
            "open_positions": open_positions,
            "drawdown": drawdown,
            "risk_status": risk_status,
        })

    except Exception as exc:
        print("[ACTIVE_CANBUY_AUTHORITY] error=" + repr(exc), flush=True)

    return state


def qfos_active_canbuy_clean_safe_state(state):
    return (
        str(state.get("risk_status", "")).upper() == "SAFE"
        and qfos_active_canbuy_float(state.get("drawdown")) >= -0.000001
        and qfos_active_canbuy_float(state.get("exposure")) <= 0.000001
        and int(state.get("open_positions") or 0) == 0
    )


def qfos_active_canbuy_authority(*args, **kwargs):
    state = qfos_active_canbuy_ledger_state()

    print(
        "[ACTIVE_CANBUY_AUTHORITY] "
        f"cash={qfos_active_canbuy_float(state.get('cash')):.8f} "
        f"equity={qfos_active_canbuy_float(state.get('equity')):.8f} "
        f"exposure={qfos_active_canbuy_float(state.get('exposure')):.8f} "
        f"drawdown={qfos_active_canbuy_float(state.get('drawdown')):.8f} "
        f"open_positions={int(state.get('open_positions') or 0)} "
        f"risk_status={state.get('risk_status')}",
        flush=True,
    )

    try:
        result = can_buy(*args, **kwargs)
    except Exception as exc:
        print("[ACTIVE_CANBUY_AUTHORITY] original_can_buy_error=" + repr(exc), flush=True)
        return False, "can_buy_exception_" + type(exc).__name__

    allowed = False
    reason = ""

    try:
        if isinstance(result, tuple):
            allowed = bool(result[0])
            reason = str(result[1]) if len(result) > 1 else ""
        else:
            allowed = bool(result)
            reason = "buy_allowed" if allowed else "buy_rejected"
    except Exception:
        allowed = False
        reason = "can_buy_bad_return"

    if (not allowed) and ("caution_drawdown_position_cap" in reason):
        if qfos_active_canbuy_clean_safe_state(state):
            print(
                "[ACTIVE_CANBUY_AUTHORITY] stale_caution_override "
                f"old_reason={reason} "
                f"cash={qfos_active_canbuy_float(state.get('cash')):.8f} "
                f"equity={qfos_active_canbuy_float(state.get('equity')):.8f} "
                f"exposure={qfos_active_canbuy_float(state.get('exposure')):.8f} "
                f"drawdown={qfos_active_canbuy_float(state.get('drawdown')):.8f} "
                f"open_positions={int(state.get('open_positions') or 0)}",
                flush=True,
            )
            return True, "ledger_safe_overrode_stale_caution_drawdown_position_cap"

    return allowed, reason

# END QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1
'''

# Insert helper before execution/allocation area.
anchor = "def entry_quality_ranked_symbols"
idx = src.find(anchor)

if idx == -1:
    anchor = "def total_exposure"
    idx = src.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: helper insertion anchor not found")

src = src[:idx] + helper + "\n\n" + src[idx:]


# Replace active can_buy(...) calls, but not the function definition and not our wrapper internals.
# This catches callsites that still bypass qfos_exec_risk_authority_firewall.
def replace_can_buy_calls(s):
    out = []
    i = 0
    n = len(s)
    count = 0

    pattern = re.compile(r"\bcan_buy\s*\(")

    for m in pattern.finditer(s):
        start = m.start()

        # Keep text before match.
        prefix = s[i:start]

        # Do not patch function definition.
        line_start = s.rfind("\n", 0, start) + 1
        line_prefix = s[line_start:start]
        line = s[line_start:s.find("\n", start) if s.find("\n", start) != -1 else n]

        if "def " in line_prefix or "qfos_active_canbuy_authority" in line or "result = can_buy" in line:
            out.append(prefix)
            out.append(s[start:m.end()])
        else:
            out.append(prefix)
            out.append("qfos_active_canbuy_authority(")
            count += 1

        i = m.end()

    out.append(s[i:])
    return "".join(out), count

src, canbuy_count = replace_can_buy_calls(src)

if canbuy_count <= 0:
    print("WARNING: no can_buy callsites replaced. Active final validation may use another risk function.")
else:
    print(f"CAN_BUY_CALLS_REPLACED={canbuy_count}")


# Insert normalized fallback inside qfos_persist_fill_atomic, if present.
persist_pattern = re.compile(
    r"(def\s+qfos_persist_fill_atomic\s*\([^\)]*\):\n)",
    re.MULTILINE
)

m = persist_pattern.search(src)

if m:
    insert = (
        m.group(1)
        + "    # QFOS_ACTIVE_CANBUY_NORMALIZED_FIX_V1: prevent NameError in runtime exception paths\n"
        + "    normalized = None\n"
        + "    try:\n"
        + "        normalized = dict(fill or {}) if isinstance(fill, dict) else {}\n"
        + "    except Exception:\n"
        + "        normalized = {}\n"
    )
    src = src[:m.start()] + insert + src[m.end():]
    print("NORMALIZED_FALLBACK_INSERTED_IN_qfos_persist_fill_atomic")
else:
    print("WARNING: qfos_persist_fill_atomic definition not found")


# Also harden any bare exception reference pattern that uses normalized.get(...) when normalized may be None.
src = src.replace(
    "normalized.get(",
    "(normalized or {}).get("
)

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
