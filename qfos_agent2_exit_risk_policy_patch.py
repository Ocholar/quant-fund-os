from pathlib import Path
import re

path = Path("main.py")
src = path.read_text(encoding="utf-8")

marker = "# QFOS_AGENT2_EXIT_RISK_POLICY_V1"

if marker in src:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''

# QFOS_AGENT2_EXIT_RISK_POLICY_V1
# Purpose:
#   Fix Agent 2 exit/risk policy failure without killing profitable quick scalps.
#
# Observed failure:
#   trailing_profit_exit was closing negative-PnL trades.
#   stop_loss exits were valid risk exits but symbols could be re-entered too soon.
#
# Rules:
#   1) trailing_profit_exit is only allowed when pnl > 0.
#   2) stop_loss exits are allowed, but the symbol is put on short risk cooldown.
#   3) BUYs are blocked during symbol risk cooldown.
#   4) take-profit and positive max-hold exits are untouched.

import time as _qfos_agent2_exit_time

_QFOS_AGENT2_SYMBOL_RISK_COOLDOWN = {}

def qfos_agent2_exit_policy_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def qfos_agent2_exit_policy_text(value):
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def qfos_agent2_exit_policy_cleanup(now=None, ttl_seconds=900):
    try:
        now = float(now or _qfos_agent2_exit_time.time())
        stale = [
            k for k, v in list(_QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.items())
            if now - float(v.get("ts", 0.0)) > ttl_seconds
        ]
        for k in stale:
            _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.pop(k, None)
    except Exception:
        pass


def qfos_agent2_exit_policy_mark_symbol(symbol, reason="", pnl=0.0, ttl_seconds=600):
    try:
        symbol = qfos_agent2_exit_policy_text(symbol)
        if not symbol:
            return

        _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN[symbol] = {
            "ts": _qfos_agent2_exit_time.time(),
            "reason": qfos_agent2_exit_policy_text(reason),
            "pnl": qfos_agent2_exit_policy_float(pnl),
            "ttl_seconds": int(ttl_seconds),
        }

        print(
            f"[AGENT2_RISK_COOLDOWN_MARK] symbol={symbol} reason={reason} pnl={pnl} ttl_seconds={ttl_seconds}",
            flush=True,
        )
    except Exception:
        pass


def qfos_agent2_exit_risk_policy_guard(conn, fill, source="unknown"):
    try:
        qfos_agent2_exit_policy_cleanup()

        if not isinstance(fill, dict):
            return True, "not_a_fill_dict"

        side = qfos_agent2_exit_policy_text(fill.get("side")).lower()
        symbol = qfos_agent2_exit_policy_text(fill.get("symbol"))
        reason = qfos_agent2_exit_policy_text(
            fill.get("exit_reason")
            or fill.get("reason")
            or fill.get("strategy")
        )
        pnl = qfos_agent2_exit_policy_float(fill.get("pnl"), 0.0)

        if not symbol:
            return True, "missing_symbol_fail_open"

        now = _qfos_agent2_exit_time.time()

        # BUY cooldown after damaging exits.
        if side == "buy":
            active = _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.get(symbol)
            if active:
                age = now - float(active.get("ts", 0.0))
                ttl_seconds = int(active.get("ttl_seconds", 600))
                if age <= ttl_seconds:
                    print(
                        f"[AGENT2_ENTRY_REJECT] symbol={symbol} reason=symbol_risk_cooldown "
                        f"age={age:.2f}s ttl_seconds={ttl_seconds} "
                        f"previous_reason={active.get('reason')} previous_pnl={active.get('pnl')} source={source}",
                        flush=True,
                    )
                    return False, "symbol_risk_cooldown"
                _QFOS_AGENT2_SYMBOL_RISK_COOLDOWN.pop(symbol, None)

            return True, "buy_exit_policy_clear"

        if side != "sell":
            return True, "non_sell_clear"

        # Hard rule: trailing profit cannot be negative or breakeven.
        if reason == "trailing_profit_exit" and pnl <= 0:
            print(
                f"[AGENT2_EXIT_REJECT] symbol={symbol} reason=negative_trailing_profit_exit "
                f"pnl={pnl:.12f} source={source}",
                flush=True,
            )
            return False, "negative_trailing_profit_exit"

        # Stop-loss exits are valid, but mark short cooldown to prevent immediate re-entry.
        if "stop_loss" in reason and pnl < 0:
            qfos_agent2_exit_policy_mark_symbol(
                symbol,
                reason=reason,
                pnl=pnl,
                ttl_seconds=600,
            )
            return True, "stop_loss_allowed_with_symbol_cooldown"

        return True, "exit_policy_clear"

    except Exception as exc:
        print(
            f"[AGENT2_EXIT_POLICY_ERROR] error={exc!r} source={source}",
            flush=True,
        )
        # Fail open to avoid trapping positions due to policy-code exception.
        return True, "exit_policy_error_fail_open"

# END QFOS_AGENT2_EXIT_RISK_POLICY_V1
'''

anchor = "def qfos_persist_fill_atomic"
idx = src.find(anchor)

if idx == -1:
    raise SystemExit("PATCH_FAILED: qfos_persist_fill_atomic anchor not found")

src = src[:idx] + helper + "\n\n" + src[idx:]

# Insert policy call at the top of qfos_persist_fill_atomic.
pattern = r'(def qfos_persist_fill_atomic\s*\([^\)]*\):\n)'
match = re.search(pattern, src)

if not match:
    raise SystemExit("PATCH_FAILED: could not match qfos_persist_fill_atomic definition")

insert = (
    match.group(1)
    + "    # QFOS_AGENT2_EXIT_RISK_POLICY_V1: enforce exit/risk policy before persistence\n"
    + "    try:\n"
    + "        _qfos_agent2_ok, _qfos_agent2_reason = qfos_agent2_exit_risk_policy_guard(conn, fill, source=source)\n"
    + "        if not _qfos_agent2_ok:\n"
    + "            return False\n"
    + "    except Exception as _qfos_agent2_exc:\n"
    + "        print(f\"[AGENT2_EXIT_POLICY_ERROR] fail_open error={_qfos_agent2_exc!r} source={source}\", flush=True)\n"
)

src = src[:match.start()] + insert + src[match.end():]

path.write_text(src, encoding="utf-8")
print("PATCH_WRITE_OK")
