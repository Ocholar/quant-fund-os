from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

guard_block = r'''
# BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1
# Ensures execution-accounting schema exists and blocks stale position resurrection.
# This does not tune strategy, dashboard, risk, fallback buys, or live trading.

import os as _qfos_schema_os
import sqlite3 as _qfos_schema_sqlite3

_QFOS_EXECUTION_SCHEMA_GUARD_DONE = globals().setdefault("_QFOS_EXECUTION_SCHEMA_GUARD_DONE", False)


def _qfos_schema_find_db_path():
    candidates = []

    env_path = _qfos_schema_os.environ.get("QFOS_DB_PATH") or _qfos_schema_os.environ.get("QUANT_DB_PATH")
    if env_path:
        candidates.append(env_path)

    database_url = _qfos_schema_os.environ.get("DATABASE_URL") or ""
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
            if path and _qfos_schema_os.path.exists(path):
                return path
        except Exception:
            pass

    return "/app/data/quant.db"


def qfos_ensure_execution_accounting_schema_and_guards(db_path=None, source="schema_guard"):
    """
    Phase 3A hard guard.

    1. Adds trades.is_exit and trades.exit_reason if missing.
    2. Adds SQLite triggers that prevent stale position resurrection:
       if latest trade for symbol is already SELL and covers the proposed
       position quantity, positions insert/update is ignored.
    """
    if db_path is None:
        db_path = _qfos_schema_find_db_path()

    if not db_path or not _qfos_schema_os.path.exists(db_path):
        return False

    conn = _qfos_schema_sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        trade_cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]

        if "is_exit" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN is_exit BOOLEAN DEFAULT 0")
            print("[QFOS_SCHEMA_GUARD] added trades.is_exit source=%s" % source, flush=True)

        trade_cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]

        if "exit_reason" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
            print("[QFOS_SCHEMA_GUARD] added trades.exit_reason source=%s" % source, flush=True)

        # Block stale INSERT restore:
        # If latest trade is SELL and its quantity covers NEW.quantity,
        # ignore attempts to recreate a positive position.
        cur.execute("DROP TRIGGER IF EXISTS qfos_block_stale_position_insert")
        cur.execute("""
        CREATE TRIGGER qfos_block_stale_position_insert
        BEFORE INSERT ON positions
        WHEN NEW.quantity > 0.00000001
         AND COALESCE((SELECT side FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), '') = 'sell'
         AND COALESCE((SELECT quantity FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), 0) + 0.00000001 >= NEW.quantity
        BEGIN
            SELECT RAISE(IGNORE);
        END;
        """)

        # Block stale UPDATE restore:
        # If a closed/zero position is repeatedly restored by sync, ignore it.
        cur.execute("DROP TRIGGER IF EXISTS qfos_block_stale_position_update")
        cur.execute("""
        CREATE TRIGGER qfos_block_stale_position_update
        BEFORE UPDATE OF quantity ON positions
        WHEN NEW.quantity > 0.00000001
         AND COALESCE((SELECT side FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), '') = 'sell'
         AND COALESCE((SELECT quantity FROM trades WHERE symbol = NEW.symbol ORDER BY id DESC LIMIT 1), 0) + 0.00000001 >= NEW.quantity
        BEGIN
            SELECT RAISE(IGNORE);
        END;
        """)

        conn.commit()
        print("[QFOS_SCHEMA_GUARD] execution accounting schema/triggers ensured source=%s" % source, flush=True)
        return True

    finally:
        conn.close()


def qfos_start_execution_accounting_schema_guard():
    global _QFOS_EXECUTION_SCHEMA_GUARD_DONE

    if _QFOS_EXECUTION_SCHEMA_GUARD_DONE:
        return False

    _QFOS_EXECUTION_SCHEMA_GUARD_DONE = True

    try:
        qfos_ensure_execution_accounting_schema_and_guards(source="startup")
    except Exception as exc:
        try:
            print("[QFOS_SCHEMA_GUARD_ERROR] error=%s" % repr(exc), flush=True)
        except Exception:
            pass

    return True


try:
    qfos_start_execution_accounting_schema_guard()
except Exception as _qfos_schema_guard_start_error:
    try:
        print("[QFOS_SCHEMA_GUARD_START_ERROR] error=%s" % repr(_qfos_schema_guard_start_error), flush=True)
    except Exception:
        pass
# END QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1
'''

# Remove previous copies if rerun.
s = re.sub(
    r"\n?# BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1.*?# END QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1\n?",
    "\n",
    s,
    flags=re.S,
)

atomic_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    flags=re.S,
)

m = atomic_re.search(s)
if not m:
    raise SystemExit("FAIL: atomic boundary block not found")

# Put schema guard after atomic boundary so it is available at module startup.
s = s[:m.end()] + "\n\n" + guard_block + "\n\n" + s[m.end():]

p.write_text(s, encoding="utf-8")
print("Inserted Phase 3A2 schema + stale-position trigger guard.")
