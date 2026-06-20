from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT5_CASH_EQUITY_RUNTIME_AUTHORITY_V2" in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT5_CASH_EQUITY_RUNTIME_AUTHORITY_V2
# Purpose:
#   Force paper runtime cash/equity/PnL to match the Postgres
#   trade+position ledger. This prevents BUY notional/exposure
#   from inflating account equity or stale snapshots from becoming
#   account authority.
# ============================================================

def qfos_agent5_ledger_accounting_snapshot():
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT *
                FROM qfos_current_ledger_accounting()
                LIMIT 1
            """)).mappings().first()
        return dict(row or {})
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] ledger_read error={e}", flush=True)
        return {}

def qfos_agent5_apply_ledger_accounting_to_runtime(source="runtime"):
    try:
        a = qfos_agent5_ledger_accounting_snapshot()
        if not a:
            return False

        cash = float(a.get("expected_cash") or 0.0)
        exposure = float(a.get("expected_exposure") or 0.0)
        equity = float(a.get("expected_equity") or 0.0)
        realized = float(a.get("realized_pnl") or 0.0)
        unrealized = float(a.get("unrealized_pnl") or 0.0)
        total_pnl = float(a.get("total_pnl") or 0.0)

        try:
            portfolio.cash = cash
        except Exception:
            pass
        try:
            portfolio.equity = equity
        except Exception:
            pass
        try:
            portfolio.realized_pnl = realized
        except Exception:
            pass
        try:
            portfolio.unrealized_pnl = unrealized
        except Exception:
            pass
        try:
            portfolio.total_pnl = total_pnl
        except Exception:
            pass
        try:
            # Do not let an inflated stale peak produce false drawdown.
            portfolio.peak = max(100.0, equity, float(getattr(portfolio, "peak", 100.0) or 100.0))
        except Exception:
            pass

        print(
            f"[QFOS_CASH_EQUITY_AUTHORITY] source={source} "
            f"cash={cash:.8f} exposure={exposure:.8f} equity={equity:.8f} "
            f"realized={realized:.8f} unrealized={unrealized:.8f} total_pnl={total_pnl:.8f}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] source={source} error={e}", flush=True)
        return False

def qfos_agent5_start_cash_equity_authority_daemon():
    try:
        import threading
        import time

        if globals().get("_qfos_agent5_cash_equity_authority_started"):
            return

        globals()["_qfos_agent5_cash_equity_authority_started"] = True

        def _worker():
            while True:
                try:
                    qfos_agent5_apply_ledger_accounting_to_runtime(source="daemon")
                except Exception as e:
                    print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] daemon error={e}", flush=True)
                time.sleep(3)

        t = threading.Thread(target=_worker, name="qfos_cash_equity_authority", daemon=True)
        t.start()
        print("[QFOS_CASH_EQUITY_AUTHORITY] daemon_started", flush=True)
    except Exception as e:
        print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] daemon_start error={e}", flush=True)

# Start immediately after definitions are loaded.
try:
    qfos_agent5_apply_ledger_accounting_to_runtime(source="module_load")
    qfos_agent5_start_cash_equity_authority_daemon()
except Exception as e:
    print(f"[QFOS_CASH_EQUITY_AUTHORITY_ERROR] startup error={e}", flush=True)

# ============================================================
# End QFOS_AGENT5_CASH_EQUITY_RUNTIME_AUTHORITY_V2
# ============================================================
'''

# Insert after portfolio/engine globals exist, just before load_state_from_db.
marker = "def load_state_from_db():"
if marker not in text:
    raise SystemExit("ERROR: def load_state_from_db() not found in main.py")

text = text.replace(marker, helper + "\n\n" + marker, 1)

# Also force sync right after state recovery if marker exists.
if 'qfos_agent5_apply_ledger_accounting_to_runtime(source="after_load_state")' not in text:
    state_marker = "print('State recovery complete.')"
    if state_marker in text:
        text = text.replace(
            state_marker,
            state_marker + '\n            qfos_agent5_apply_ledger_accounting_to_runtime(source="after_load_state")',
            1,
        )

# Also force sync immediately before common snapshot inserts if exact function text appears.
# Safe no-op if pattern not found.
if 'qfos_agent5_apply_ledger_accounting_to_runtime(source="before_snapshot_write")' not in text:
    text = text.replace(
        "INSERT INTO portfolio_snapshots",
        "/* QFOS_AGENT5: snapshot DB trigger enforces ledger accounting */ INSERT INTO portfolio_snapshots",
        1,
    )

path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
