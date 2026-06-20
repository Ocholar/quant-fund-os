from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")
before = text

start = "# QFOS_BASELINE_AUTHORITY_PATCH_START"
end = "# QFOS_BASELINE_AUTHORITY_PATCH_END"

clean_block = r'''
# QFOS_BASELINE_AUTHORITY_PATCH_START
def qfos_baseline_authority_clean_ledger_counts():
    """
    Return (trades_count, open_position_count) from the authoritative runtime DB.
    """
    try:
        with engine.begin() as conn:
            try:
                trades_count = int(conn.execute(text("SELECT COUNT(*) FROM trades")).scalar() or 0)
            except Exception:
                trades_count = 0

            try:
                open_position_count = int(conn.execute(text(
                    "SELECT COUNT(*) FROM positions WHERE COALESCE(quantity, 0) > 0"
                )).scalar() or 0)
            except Exception:
                open_position_count = 0

        return trades_count, open_position_count

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] clean_ledger_count_error={exc}", flush=True)
        return None, None


def qfos_baseline_authority_clear_redis_control_state():
    """
    Clear stale Redis automatic control state without touching manual source code.
    """
    try:
        import redis as _redis
    except Exception:
        return

    try:
        redis_url = str(getattr(settings, "redis_url", "redis://localhost:6379/0"))
        client = _redis.Redis.from_url(redis_url, decode_responses=True)

        stale_terms = (
            "max_daily_loss",
            "daily_loss",
            "near_blocked_drawdown",
            "blocked_drawdown",
            "risk_status",
            "pause_reason",
            "paused",
            "bot_state",
            "BLOCKED",
            "max_daily_loss_hit",
            "92.54",
            "5.90",
            "5.43",
            "control",
        )

        deleted = []

        for raw_key in client.scan_iter("*"):
            key = str(raw_key)
            key_l = key.lower()
            delete = any(term.lower() in key_l for term in stale_terms)

            if not delete:
                try:
                    val = client.get(key)
                    haystack = f"{key} {val}".lower()
                    delete = any(term.lower() in haystack for term in stale_terms)
                except Exception:
                    delete = False

            if delete:
                try:
                    client.delete(key)
                    deleted.append(key)
                except Exception:
                    pass

        if deleted:
            print(f"[BASELINE_AUTHORITY] redis_stale_control_keys_deleted={deleted}", flush=True)
        else:
            print("[BASELINE_AUTHORITY] redis_no_stale_control_keys_found", flush=True)

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] redis_clear_error={exc}", flush=True)

    try:
        qfos_restore_pause_reason_callable()
    except Exception:
        pass


def qfos_baseline_authority_clear_control_module_state():
    """
    Clear automatic pause state while preserving pause_reason as callable.
    """
    try:
        import core.control as _control

        for name in ("paused", "_paused", "PAUSED", "is_bot_paused"):
            if hasattr(_control, name):
                try:
                    setattr(_control, name, False)
                except Exception:
                    pass

        for name in ("pause_reason", "_pause_reason", "PAUSE_REASON", "last_pause_reason"):
            if hasattr(_control, name):
                try:
                    current_attr = getattr(_control, name, None)
                    if name == "pause_reason" and callable(current_attr):
                        pass
                    else:
                        setattr(_control, name, "")
                except Exception:
                    pass

        for fn_name in ("resume_bot", "unpause_bot", "clear_pause", "clear_pause_reason"):
            fn = getattr(_control, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except TypeError:
                    try:
                        fn("")
                    except Exception:
                        pass
                except Exception:
                    pass

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] control_module_clear_error={exc}", flush=True)

    try:
        qfos_restore_pause_reason_callable()
    except Exception:
        pass


def qfos_baseline_authority_write_clean_snapshot(regime="SIDEWAYS"):
    """
    Write a clean baseline snapshot using available columns.
    """
    try:
        import datetime as _dt

        with engine.begin() as conn:
            try:
                rows = conn.execute(text("SELECT * FROM portfolio_snapshots LIMIT 0"))
                cols = list(rows.keys())
            except Exception:
                cols = []

            if not cols:
                return

            now_s = _dt.datetime.utcnow().isoformat()

            values = {}

            if "equity" in cols:
                values["equity"] = float(INITIAL_EQUITY)
            if "cash" in cols:
                values["cash"] = float(INITIAL_EQUITY)
            if "exposure" in cols:
                values["exposure"] = 0.0
            if "drawdown" in cols:
                values["drawdown"] = 0.0
            if "regime" in cols:
                values["regime"] = str(regime or "SIDEWAYS")
            if "realized_pnl" in cols:
                values["realized_pnl"] = 0.0
            if "unrealized_pnl" in cols:
                values["unrealized_pnl"] = 0.0
            if "total_pnl" in cols:
                values["total_pnl"] = 0.0
            if "created_at" in cols:
                values["created_at"] = now_s
            if "updated_at" in cols:
                values["updated_at"] = now_s
            if "timestamp" in cols:
                values["timestamp"] = now_s

            if not values:
                return

            col_sql = ", ".join(values.keys())
            bind_sql = ", ".join([f":{k}" for k in values.keys()])

            conn.execute(
                text(f"INSERT INTO portfolio_snapshots ({col_sql}) VALUES ({bind_sql})"),
                values,
            )

            print("[BASELINE_AUTHORITY] clean_baseline_snapshot_written", flush=True)

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] snapshot_write_error={exc}", flush=True)


def qfos_clean_runtime_state_if_db_baseline(reason="runtime"):
    """
    Hard clean-ledger runtime authority.

    If trades=0 and open_positions=0, stale runtime/risk/pause state must not survive.
    """
    try:
        trades_count, open_position_count = qfos_baseline_authority_clean_ledger_counts()

        if trades_count is None or open_position_count is None:
            return False

        if int(trades_count) != 0 or int(open_position_count) != 0:
            return False

        try:
            if hasattr(portfolio, "reset") and callable(getattr(portfolio, "reset")):
                portfolio.reset(float(INITIAL_EQUITY))
            else:
                portfolio.cash = float(INITIAL_EQUITY)
                portfolio.equity = float(INITIAL_EQUITY)
                portfolio.peak = float(INITIAL_EQUITY)

                if hasattr(portfolio, "positions"):
                    portfolio.positions.clear()
                if hasattr(portfolio, "avg_entry"):
                    portfolio.avg_entry.clear()
                if hasattr(portfolio, "realized_pnl"):
                    portfolio.realized_pnl = 0.0
                if hasattr(portfolio, "unrealized_pnl"):
                    portfolio.unrealized_pnl = 0.0

        except Exception as exc:
            print(f"[BASELINE_AUTHORITY] portfolio_reset_error={exc}", flush=True)

        for name in (
            "entry_prices",
            "position_open_time",
            "position_peak_change",
            "shadow_positions",
            "shadow_entry_prices",
            "shadow_trade_counts",
            "trade_counts",
            "last_trade_time",
            "quarantined_symbols",
        ):
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
            except Exception:
                pass

        for name in ("rejected", "rejected_orders", "last_rejected", "recent_rejections"):
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
                elif name in globals():
                    globals()[name] = []
            except Exception:
                pass

        try:
            globals()["current_risk_status"] = "SAFE"
            globals()["risk_status"] = "SAFE"
            globals()["bot_state"] = "RUNNING"
            globals()["paused"] = False
            globals()["pause_reason_value"] = ""
            globals()["last_risk_status"] = "SAFE"
            globals()["last_auto_pause_reason"] = None
        except Exception:
            pass

        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        for name in ("risk_engine", "risk", "engine_risk"):
            try:
                obj = globals().get(name)
                if obj is not None and hasattr(obj, "reset_risk_state"):
                    obj.reset_risk_state(float(INITIAL_EQUITY))
            except Exception:
                pass

        qfos_baseline_authority_clear_redis_control_state()
        qfos_baseline_authority_clear_control_module_state()

        try:
            qfos_restore_pause_reason_callable()
        except Exception:
            pass

        try:
            regime = str(globals().get("regime", "SIDEWAYS") or "SIDEWAYS")
        except Exception:
            regime = "SIDEWAYS"

        qfos_baseline_authority_write_clean_snapshot(regime=regime)

        print(
            "[BASELINE_AUTHORITY] clean_ledger_runtime_reset_applied "
            f"reason={reason} trades={trades_count} open_positions={open_position_count} "
            f"equity={float(INITIAL_EQUITY):.2f} cash={float(INITIAL_EQUITY):.2f} "
            "exposure=0 drawdown=0 risk_status=SAFE paused=False pause_reason=''",
            flush=True,
        )

        return True

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] reset_error={exc}", flush=True)
        return False
# QFOS_BASELINE_AUTHORITY_PATCH_END
'''

pattern = re.compile(
    re.escape(start) + r".*?" + re.escape(end),
    flags=re.DOTALL,
)

text, count = pattern.subn(clean_block.strip(), text, count=1)

if count != 1:
    raise SystemExit(f"PATCH_FAILED: expected exactly one {start}/{end} block, replaced={count}")

# Fix any malformed function definitions left by accidental insertions.
text = re.sub(
    r"(?m)^(\s*)def\s+(qfos_[A-Za-z0-9_]+)\(([^)]*)\)\s*$",
    r"\1def \2(\3):",
    text,
)

# Fix accidental pass: lines.
text = re.sub(r"(?m)^(\s*)pass:\s*$", r"\1pass", text)

# Do not overwrite pause_reason callable.
text = text.replace('globals()["pause_reason"] = ""', 'globals()["pause_reason_value"] = ""')
text = text.replace("globals()['pause_reason'] = ''", "globals()['pause_reason_value'] = ''")

text = re.sub(
    r"(?m)^(\s*)pause_reason\s*=\s*['\"]{2}\s*$",
    r"\1pause_reason_value = ''",
    text,
)

# Ensure any direct pause_reason() calls are converted to the safe helper.
text = text.replace("pause_reason()", "qfos_safe_pause_reason_text()")
text = text.replace("qfos_safe_qfos_safe_pause_reason_text_text()", "qfos_safe_pause_reason_text()")

# Ensure the helper exists. If the prior helper was damaged, leave existing one only if marker exists.
if "QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason" not in text:
    import_line = "from core.control import is_paused, pause_bot, pause_reason"
    if import_line not in text:
        raise SystemExit("PATCH_FAILED: core.control import not found")
    text = text.replace(
        import_line,
        import_line + "\nQFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason",
        1,
    )

if "# QFOS_PAUSE_REASON_CALLABLE_GUARD_START" not in text:
    helper = '''
# QFOS_PAUSE_REASON_CALLABLE_GUARD_START
def qfos_safe_pause_reason_text():
    try:
        import core.control as _control
        fn = getattr(_control, "pause_reason", None)
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass
    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass
    try:
        val = globals().get("pause_reason", "")
        if callable(val):
            return str(val() or "")
        return str(val or "")
    except Exception:
        return ""


def qfos_restore_pause_reason_callable():
    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            globals()["pause_reason"] = fn
            try:
                import core.control as _control
                if not callable(getattr(_control, "pause_reason", None)):
                    setattr(_control, "pause_reason", fn)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False
# QFOS_PAUSE_REASON_CALLABLE_GUARD_END
'''
    text = text.replace(
        "QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason",
        "QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason\n\n" + helper.strip(),
        1,
    )

path.write_text(text, encoding="utf-8")

print("BASELINE_BLOCK_REPLACED_OK")
print("REPLACED_BLOCKS", count)
print("CHANGED", text != before)
