from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1" in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1
# Purpose:
#   If Postgres ledger is clean paper baseline, force runtime
#   in-memory portfolio and pause/drawdown state to agree.
#   This prevents /status from showing stale cash/equity/drawdown
#   after orphan position cleanup.
# ============================================================

def qfos_force_runtime_clean_baseline_if_db_clean():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM trades) AS trades_n,
                    (SELECT COUNT(*) FROM positions WHERE quantity > 0.00000001) AS open_n,
                    ps.equity,
                    ps.cash,
                    ps.exposure,
                    ps.drawdown
                FROM portfolio_snapshots ps
                ORDER BY ps.id DESC
                LIMIT 1
            """)).mappings().first()

        if not row:
            return False

        trades_n = int(row.get("trades_n") or 0)
        open_n = int(row.get("open_n") or 0)
        equity = float(row.get("equity") or 0.0)
        cash = float(row.get("cash") or 0.0)
        exposure = float(row.get("exposure") or 0.0)
        drawdown = float(row.get("drawdown") or 0.0)

        is_clean_db = (
            trades_n == 0
            and open_n == 0
            and abs(equity - 100.0) < 0.0001
            and abs(cash - 100.0) < 0.0001
            and abs(exposure) < 0.0001
            and abs(drawdown) < 0.0001
        )

        if not is_clean_db:
            return False

        try:
            portfolio.cash = 100.0
            portfolio.equity = 100.0
            portfolio.peak = 100.0
        except Exception:
            pass

        try:
            portfolio.positions.clear()
        except Exception:
            pass

        for obj_name in [
            "entry_prices",
            "position_open_time",
            "position_peak_change",
            "shadow_positions",
            "shadow_entry_prices",
            "shadow_trade_counts",
            "trade_counts",
            "last_trade_time",
        ]:
            try:
                obj = globals().get(obj_name)
                if hasattr(obj, "clear"):
                    obj.clear()
            except Exception:
                pass

        try:
            # Clear stale pause only when it is clearly caused by the orphan-ledger stale drawdown.
            pr = str(globals().get("pause_reason", "") or "")
            if "max_daily_loss_hit" in pr or "-5.43" in pr:
                globals()["pause_reason"] = ""
        except Exception:
            pass

        print(
            "[QFOS_RUNTIME_BASELINE_SYNC] forced runtime clean baseline "
            "equity=100 cash=100 exposure=0 positions=0",
            flush=True,
        )
        return True

    except Exception as e:
        print(f"[QFOS_RUNTIME_BASELINE_SYNC_ERROR] error={e}", flush=True)
        return False

# ============================================================
# End QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1
# ============================================================
'''

# Insert helper before the existing Agent 1 clean baseline guard or before startup prints.
insert_marker = "# ============================================================\n# QFOS_AGENT1_CLEAN_BASELINE_RUNTIME_GUARD_V1"
if insert_marker in text:
    text = text.replace(insert_marker, helper + "\n\n" + insert_marker, 1)
else:
    fallback = "qfos_clean_runtime_state_if_db_baseline()"
    if fallback not in text:
        raise SystemExit("ERROR: could not find startup baseline guard location")
    text = text.replace(fallback, helper + "\n\n" + fallback, 1)

# Ensure the new guard is called immediately after the old guard call if present.
call = "qfos_force_runtime_clean_baseline_if_db_clean()"
if call not in text:
    old_call = "qfos_clean_runtime_state_if_db_baseline()"
    if old_call in text:
        text = text.replace(old_call, old_call + "\n" + call, 1)
    else:
        raise SystemExit("ERROR: could not insert runtime baseline sync call")

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
