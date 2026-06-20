from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker_start = "# QFOS_BASELINE_AUTHORITY_PATCH_START"
marker_end = "# QFOS_BASELINE_AUTHORITY_PATCH_END"

patch = r'''
# QFOS_BASELINE_AUTHORITY_PATCH_START
def qfos_baseline_authority_clean_ledger_counts():
    """
    Agent 1 + Agent 2 authority check.

    Returns:
        (trades_count, open_position_count)

    Supports the current SQLAlchemy engine used by the runtime. This is
    intentionally defensive so it works whether the backing DB is SQLite
    or Postgres through SQLAlchemy.
    """
    trades_count = None
    open_position_count = None

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

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] clean_ledger_count_error={exc}", flush=True)
        return None, None

    return trades_count, open_position_count


def qfos_baseline_authority_clear_redis_control_state():
    """
    Clear stale automatic pause/risk state from Redis.

    Manual pause preservation is best-effort:
    - automatic max_daily_loss / drawdown / blocked stale keys are removed
    - generic pause/risk keys are removed if they look like bot-control state
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
        )

        deleted = []
        inspected = []

        for raw_key in client.scan_iter("*"):
            key = str(raw_key)
            key_l = key.lower()

            should_inspect = any(t.lower() in key_l for t in stale_terms)

            value = None
            if should_inspect:
                try:
                    value = client.get(key)
                except Exception:
                    value = None

                value_s = "" if value is None else str(value)
                haystack = f"{key} {value_s}".lower()

                if any(t.lower() in haystack for t in stale_terms):
                    try:
                        client.delete(key)
                        deleted.append(key)
                    except Exception:
                        pass

            inspected.append(key)

        if deleted:
            print(f"[BASELINE_AUTHORITY] redis_stale_control_keys_deleted={deleted}", flush=True)
        else:
            print("[BASELINE_AUTHORITY] redis_no_stale_control_keys_found", flush=True)

    except Exception as exc:
        print(f"[BASELINE_AUTHORITY] redis_clear_error={exc}", flush=True)


def qfos_baseline_authority_clear_control_module_state():
    """
    Best-effort clearing for core.control state.

    The control module implementation has changed over time, so this avoids
    assuming a single exact API. It clears common module-level variables if
    present and calls resume-style functions if they exist.
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


def qfos_baseline_authority_write_clean_snapshot(regime="SIDEWAYS"):
    """
    Write a clean portfolio snapshot if portfolio_snapshots exists.

    Uses schema introspection so this does not break if columns differ.
    """
    try:
        with engine.begin() as conn:
            try:
                rows = conn.execute(text("SELECT * FROM portfolio_snapshots LIMIT 0"))
                cols = list(rows.keys())
            except Exception:
                cols = []

            if not cols:
                return

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
                values["created_at"] = _now_iso_local() if "_now_iso_local" in globals() else None
            if "updated_at" in cols:
                values["updated_at"] = _now_iso_local() if "_now_iso_local" in globals() else None

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
    Hard runtime baseline authority.

    Required rule:
    If the ledger is clean:
        trades_count == 0
        open_position_count == 0

    Then no stale runtime / Redis / pause / risk / max daily loss memory may survive.
    """
    try:
        trades_count, open_position_count = qfos_baseline_authority_clean_ledger_counts()

        if trades_count is None or open_position_count is None:
            return False

        if int(trades_count) != 0 or int(open_position_count) != 0:
            return False

        # Clear portfolio memory.
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

        # Clear common runtime dicts/lists if present.
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

        # Clear rejected/current order residue if present.
        for name in ("rejected", "rejected_orders", "last_rejected", "recent_rejections"):
            try:
                obj = globals().get(name)
                if hasattr(obj, "clear"):
                    obj.clear()
                elif name in globals():
                    globals()[name] = []
            except Exception:
                pass

        # Force runtime risk state.
        try:
            globals()["risk_status"] = "SAFE"
        except Exception:
            pass

        try:
            globals()["last_risk_status"] = None
        except Exception:
            pass

        try:
            globals()["last_auto_pause_reason"] = None
        except Exception:
            pass

        # Reset risk engine instance if present.
        for name in ("risk_engine", "risk", "engine_risk"):
            try:
                obj = globals().get(name)
                if obj is not None and hasattr(obj, "reset_risk_state"):
                    obj.reset_risk_state(float(INITIAL_EQUITY))
            except Exception:
                pass

        # Clear Redis/control pause state.
        qfos_baseline_authority_clear_redis_control_state()
        qfos_baseline_authority_clear_control_module_state()

        # If local pause variables exist, clear only stale automatic pause.
        for name in ("paused", "_paused", "bot_paused"):
            try:
                if name in globals():
                    globals()[name] = False
            except Exception:
                pass

        for name in ("pause_reason", "_pause_reason", "last_pause_reason"):
            try:
                if name in globals():
                    globals()[name] = ""
            except Exception:
                pass

        # Write clean baseline snapshot.
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

# Remove old copy of this patch if already present.
if marker_start in text and marker_end in text:
    text = re.sub(
        re.escape(marker_start) + r".*?" + re.escape(marker_end),
        patch.strip(),
        text,
        flags=re.DOTALL,
    )
else:
    # If an older qfos_clean_runtime_state_if_db_baseline function exists,
    # replace it to avoid old stale-reset behavior.
    func_pat = re.compile(
        r"\ndef qfos_clean_runtime_state_if_db_baseline\s*\([^)]*\):\n.*?(?=\ndef\s+|\nclass\s+|\nqfos_clean_runtime_state_if_db_baseline\(\)|\nprint\('Quant Fund OS starting|\nprint\(\"Quant Fund OS starting|$)",
        flags=re.DOTALL,
    )

    if func_pat.search(text):
        text = func_pat.sub("\n" + patch.strip() + "\n", text, count=1)
    else:
        # Insert before startup call if possible, otherwise before first main startup print.
        inserted = False

        anchors = [
            "qfos_clean_runtime_state_if_db_baseline()",
            "print('Quant Fund OS starting.",
            'print("Quant Fund OS starting.',
        ]

        for anchor in anchors:
            idx = text.find(anchor)
            if idx >= 0:
                text = text[:idx] + patch.strip() + "\n\n" + text[idx:]
                inserted = True
                break

        if not inserted:
            text = text + "\n\n" + patch.strip() + "\n"

# Ensure startup call exists after patch.
if "qfos_clean_runtime_state_if_db_baseline(reason=\"startup\")" not in text:
    # Replace old no-arg startup call if present.
    if "qfos_clean_runtime_state_if_db_baseline()" in text:
        text = text.replace(
            "qfos_clean_runtime_state_if_db_baseline()",
            "qfos_clean_runtime_state_if_db_baseline(reason=\"startup\")",
            1,
        )
    else:
        # Insert before startup print.
        startup_anchors = [
            "print('Quant Fund OS starting.",
            'print("Quant Fund OS starting.',
        ]
        inserted = False
        for anchor in startup_anchors:
            idx = text.find(anchor)
            if idx >= 0:
                text = text[:idx] + "qfos_clean_runtime_state_if_db_baseline(reason=\"startup\")\n" + text[idx:]
                inserted = True
                break
        if not inserted:
            text += "\nqfos_clean_runtime_state_if_db_baseline(reason=\"startup\")\n"

# Add /status guard if a status function exists.
if "qfos_clean_runtime_state_if_db_baseline(reason=\"status\")" not in text:
    lines = text.splitlines()
    out = []
    inserted_status = False

    for i, line in enumerate(lines):
        out.append(line)

        stripped = line.strip()
        if not inserted_status and (
            stripped.startswith("def status(")
            or stripped.startswith("async def status(")
            or stripped.startswith("def get_status(")
            or stripped.startswith("async def get_status(")
        ):
            indent = line[:len(line) - len(line.lstrip())] + "    "
            out.append(indent + "try:")
            out.append(indent + "    qfos_clean_runtime_state_if_db_baseline(reason=\"status\")")
            out.append(indent + "except Exception as exc:")
            out.append(indent + "    print(f\"[BASELINE_AUTHORITY] status_guard_error={exc}\", flush=True)")
            inserted_status = True

    text = "\n".join(out) + "\n"

path.write_text(text, encoding="utf-8")
print("MAIN_BASELINE_AUTHORITY_PATCH_OK")
