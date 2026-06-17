from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    re.S,
)

m = block_re.search(s)
if not m:
    raise SystemExit("FAIL: atomic boundary block not found")

block = m.group(0)

# Add SQLAlchemy text import inside block if missing.
if "from sqlalchemy import text as _qfos_sa_text" not in block:
    block = block.replace(
        "from datetime import datetime as _qfos_datetime",
        "from datetime import datetime as _qfos_datetime\ntry:\n    from sqlalchemy import text as _qfos_sa_text\nexcept Exception:\n    _qfos_sa_text = None",
        1
    )

# Add generic SQL execution helpers after _QFOS_EPSILON.
helper = r'''

def _qfos_is_sqlalchemy_conn(conn):
    return hasattr(conn, "execute") and conn.__class__.__module__.startswith("sqlalchemy")


def _qfos_exec(conn, sql, params=None):
    """
    Executes SQL against either raw sqlite3 connections or SQLAlchemy 2.x connections.
    SQLAlchemy requires text(sql) and dict parameters.
    """
    if params is None:
        params = {}

    if _qfos_is_sqlalchemy_conn(conn):
        if _qfos_sa_text is None:
            raise RuntimeError("SQLAlchemy connection detected but sqlalchemy.text unavailable")

        if isinstance(params, (tuple, list)):
            raise RuntimeError("SQLAlchemy execution requires named dict parameters")

        return conn.execute(_qfos_sa_text(sql), params)

    if isinstance(params, dict):
        return conn.execute(sql, params)

    return conn.execute(sql, params)


def _qfos_commit(conn):
    try:
        conn.commit()
    except Exception:
        pass


def _qfos_rollback(conn):
    try:
        conn.rollback()
    except Exception:
        pass
'''

if "def _qfos_exec(conn, sql, params=None):" not in block:
    block = block.replace("_QFOS_EPSILON = 1e-12\n", "_QFOS_EPSILON = 1e-12\n" + helper + "\n", 1)

# Replace table columns function.
block = re.sub(
    r"def _qfos_table_columns\(conn, table_name\):\n\s+rows = conn\.execute\(f\"PRAGMA table_info\(\{table_name\}\)\"\)\.fetchall\(\)\n\s+return \[r\[1\] for r in rows\]",
    '''def _qfos_table_columns(conn, table_name):
    rows = _qfos_exec(conn, f"PRAGMA table_info({table_name})").fetchall()
    return [r[1] for r in rows]''',
    block,
    flags=re.S,
)

# Replace sqlite positional SELECT in _qfos_get_position_row.
block = block.replace(
'''    row = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=? LIMIT 1",
        (symbol,)
    ).fetchone()''',
'''    row = _qfos_exec(
        conn,
        f"SELECT {', '.join(select_cols)} FROM positions WHERE symbol=:symbol LIMIT 1",
        {"symbol": symbol}
    ).fetchone()'''
)

# Replace exists query.
block = block.replace(
'''    exists = conn.execute("SELECT 1 FROM positions WHERE symbol=? LIMIT 1", (symbol,)).fetchone() is not None''',
'''    exists = _qfos_exec(
        conn,
        "SELECT 1 FROM positions WHERE symbol=:symbol LIMIT 1",
        {"symbol": symbol}
    ).fetchone() is not None'''
)

# Replace UPDATE positions execution.
block = block.replace(
'''        conn.execute(
            f"UPDATE positions SET {assignments} WHERE symbol=?",
            list(values.values()) + [symbol]
        )''',
'''        params = dict(values)
        params["symbol"] = symbol
        named_assignments = ", ".join([f"{k}=:{k}" for k in values.keys()])
        _qfos_exec(
            conn,
            f"UPDATE positions SET {named_assignments} WHERE symbol=:symbol",
            params
        )'''
)

# Replace INSERT positions execution.
block = block.replace(
'''        conn.execute(
            f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({placeholders})",
            [symbol] + list(values.values())
        )''',
'''        params = {"symbol": symbol}
        params.update(values)
        named_placeholders = ", ".join([f":{c}" for c in insert_cols])
        _qfos_exec(
            conn,
            f"INSERT INTO positions ({', '.join(insert_cols)}) VALUES ({named_placeholders})",
            params
        )'''
)

# Replace INSERT trades execution.
block = block.replace(
'''    placeholders = ", ".join(["?"] * len(insert_cols))
    conn.execute(
        f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})",
        insert_vals
    )''',
'''    params = {col: val for col, val in zip(insert_cols, insert_vals)}
    placeholders = ", ".join([f":{col}" for col in insert_cols])
    _qfos_exec(
        conn,
        f"INSERT INTO trades ({', '.join(insert_cols)}) VALUES ({placeholders})",
        params
    )'''
)

# Replace latest trade SELECT.
block = block.replace(
'''    row = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM trades WHERE symbol=? ORDER BY {order_col} DESC LIMIT 1",
        (symbol,)
    ).fetchone()''',
'''    row = _qfos_exec(
        conn,
        f"SELECT {', '.join(select_cols)} FROM trades WHERE symbol=:symbol ORDER BY {order_col} DESC LIMIT 1",
        {"symbol": symbol}
    ).fetchone()'''
)

# Replace BEGIN IMMEDIATE / commit / rollback.
block = block.replace('conn.execute("BEGIN IMMEDIATE")', '_qfos_exec(conn, "BEGIN IMMEDIATE")')
block = block.replace("conn.commit()", "_qfos_commit(conn)")
block = block.replace("conn.rollback()", "_qfos_rollback(conn)")

s = s[:m.start()] + block + s[m.end():]
p.write_text(s, encoding="utf-8")

print("Patched atomic boundary for SQLAlchemy-compatible SQL execution.")
