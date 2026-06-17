"""
QFOS runtime DB path guard.

Purpose:
- Force all runtime SQLite access to /app/data/quant.db inside Docker.
- Prevent late/background workers from falling back to data/quant.db, ./data/quant.db,
  quant.db, or /app/quant.db.
- Keep live_trading and strategy/risk/allocation/execution logic untouched.

Python automatically imports this file at interpreter startup when the repo root is on sys.path.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any

QFOS_CANONICAL_DB_PATH = os.environ.get("QFOS_DB_PATH") or os.environ.get("DB_PATH") or "/app/data/quant.db"
QFOS_CANONICAL_DB_PATH = os.path.abspath(QFOS_CANONICAL_DB_PATH)

os.environ["DB_PATH"] = QFOS_CANONICAL_DB_PATH
os.environ["DATABASE_PATH"] = QFOS_CANONICAL_DB_PATH
os.environ["SQLITE_DB_PATH"] = QFOS_CANONICAL_DB_PATH
os.environ["QFOS_DB_PATH"] = QFOS_CANONICAL_DB_PATH
os.environ["QUANT_DB_PATH"] = QFOS_CANONICAL_DB_PATH
os.environ["DATABASE_URL"] = "sqlite:///" + QFOS_CANONICAL_DB_PATH

_PARENT = os.path.dirname(QFOS_CANONICAL_DB_PATH)
os.makedirs(_PARENT, exist_ok=True)

_BAD_SQLITE_PATHS = {
    "quant.db",
    "./quant.db",
    "data/quant.db",
    "./data/quant.db",
    "/app/quant.db",
    "sqlite:///quant.db",
    "sqlite:///./quant.db",
    "sqlite:///data/quant.db",
    "sqlite:///./data/quant.db",
    "sqlite:////app/quant.db",
}

def _normalize_db_arg(database: Any) -> Any:
    try:
        raw = os.fspath(database)
    except TypeError:
        return database

    s = str(raw).replace("\\", "/").strip()

    if s in _BAD_SQLITE_PATHS:
        return QFOS_CANONICAL_DB_PATH

    if s.endswith("/quant.db"):
        # Keep the canonical path canonical; prevent /app/quant.db and relative split paths.
        if s != QFOS_CANONICAL_DB_PATH:
            if s in {"data/quant.db", "./data/quant.db", "quant.db", "./quant.db", "/app/quant.db"}:
                return QFOS_CANONICAL_DB_PATH

    return database

_original_sqlite_connect = sqlite3.connect

def _qfos_sqlite_connect(database: Any, *args: Any, **kwargs: Any):
    database = _normalize_db_arg(database)
    kwargs.setdefault("timeout", float(os.environ.get("QFOS_SQLITE_TIMEOUT", "30")))
    conn = _original_sqlite_connect(database, *args, **kwargs)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn

sqlite3.connect = _qfos_sqlite_connect

# Patch SQLAlchemy create_engine if SQLAlchemy is installed/importable.
try:
    import sqlalchemy as _sqlalchemy

    _original_create_engine = _sqlalchemy.create_engine

    def _qfos_create_engine(url: Any, *args: Any, **kwargs: Any):
        try:
            raw = str(url).replace("\\", "/").strip()
            if raw in _BAD_SQLITE_PATHS or raw in {
                "sqlite:///quant.db",
                "sqlite:///data/quant.db",
                "sqlite:///./data/quant.db",
                "sqlite:////app/quant.db",
            }:
                url = "sqlite:///" + QFOS_CANONICAL_DB_PATH
        except Exception:
            pass

        if str(url).startswith("sqlite"):
            connect_args = dict(kwargs.get("connect_args") or {})
            connect_args.setdefault("timeout", float(os.environ.get("QFOS_SQLITE_TIMEOUT", "30")))
            connect_args.setdefault("check_same_thread", False)
            kwargs["connect_args"] = connect_args

        return _original_create_engine(url, *args, **kwargs)

    _sqlalchemy.create_engine = _qfos_create_engine
except Exception:
    pass

print(f"[QFOS_DB_PATH_GUARD] canonical_db_path={QFOS_CANONICAL_DB_PATH}", flush=True)
