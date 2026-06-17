from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

daemon_block = r'''
# BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1
# Runtime safety daemon:
# Automatically repairs positions resurrected by stale paper_position_sync after a full SELL.
# It inserts no trade rows and does not change strategy thresholds.

import os as _qfos_os
import time as _qfos_time
import threading as _qfos_threading
import sqlite3 as _qfos_sqlite3

_QFOS_STALE_RECONCILER_STARTED = globals().setdefault("_QFOS_STALE_RECONCILER_STARTED", False)


def _qfos_find_sqlite_db_path():
    candidates = []

    env_path = _qfos_os.environ.get("QFOS_DB_PATH") or _qfos_os.environ.get("QUANT_DB_PATH")
    if env_path:
        candidates.append(env_path)

    database_url = _qfos_os.environ.get("DATABASE_URL") or ""
    if database_url.startswith("sqlite:///"):
        candidates.append(database_url.replace("sqlite:///", "", 1))

    candidates.extend([
        "/app/data/quant.db",
        "data/quant.db",
        "./data/quant.db",
        "quant.db",
    ])

    for path in candidates:
        try:
            if path and _qfos_os.path.exists(path):
                return path
        except Exception:
            pass

    # Default Docker path. It may appear after startup.
    return "/app/data/quant.db"


def qfos_run_stale_position_reconciler_once(source="auto_phase2h_reconciler"):
    try:
        db_path = _qfos_find_sqlite_db_path()
        if not db_path or not _qfos_os.path.exists(db_path):
            return []

        conn = _qfos_sqlite3.connect(db_path)
        try:
            symbols = qfos_reconcile_stale_closed_positions(conn, source=source)
            conn.commit()
            if symbols:
                print(
                    "[QFOS_AUTO_STALE_RECONCILER] reconciled=%s source=%s"
                    % (",".join(symbols), source),
                    flush=True,
                )
            return symbols
        finally:
            conn.close()
    except Exception as exc:
        try:
            print("[QFOS_AUTO_STALE_RECONCILER_ERROR] error=%s" % repr(exc), flush=True)
        except Exception:
            pass
        return []


def qfos_start_stale_position_reconciler_daemon(interval_seconds=10):
    global _QFOS_STALE_RECONCILER_STARTED

    if _QFOS_STALE_RECONCILER_STARTED:
        return False

    if str(_qfos_os.environ.get("QFOS_DISABLE_STALE_RECONCILER", "")).lower() in ("1", "true", "yes"):
        print("[QFOS_AUTO_STALE_RECONCILER_DISABLED]", flush=True)
        return False

    _QFOS_STALE_RECONCILER_STARTED = True

    def _loop():
        print(
            "[QFOS_AUTO_STALE_RECONCILER_STARTED] interval_seconds=%s"
            % interval_seconds,
            flush=True,
        )
        while True:
            qfos_run_stale_position_reconciler_once(source="auto_phase2h_reconciler")
            _qfos_time.sleep(interval_seconds)

    t = _qfos_threading.Thread(
        target=_loop,
        name="qfos_stale_position_reconciler",
        daemon=True,
    )
    t.start()
    return True


# Start at module import so it runs under uvicorn/start.sh as well as normal main loop.
try:
    qfos_start_stale_position_reconciler_daemon(interval_seconds=10)
except Exception as _qfos_reconciler_start_error:
    try:
        print(
            "[QFOS_AUTO_STALE_RECONCILER_START_ERROR] error=%s"
            % repr(_qfos_reconciler_start_error),
            flush=True,
        )
    except Exception:
        pass
# END QFOS_STALE_POSITION_RECONCILER_DAEMON_V1
'''

# Remove existing daemon block if rerun.
s = re.sub(
    r"\n?# BEGIN QFOS_STALE_POSITION_RECONCILER_DAEMON_V1.*?# END QFOS_STALE_POSITION_RECONCILER_DAEMON_V1\n?",
    "\n",
    s,
    flags=re.S,
)

atomic_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    re.S,
)
m = atomic_re.search(s)
if not m:
    raise SystemExit("FAIL: atomic boundary block not found")

# Insert daemon immediately after atomic block so qfos_reconcile_stale_closed_positions is defined first.
s = s[:m.end()] + "\n\n" + daemon_block + "\n\n" + s[m.end():]

p.write_text(s, encoding="utf-8")
print("Inserted Phase 2H automatic stale-position reconciler daemon.")
