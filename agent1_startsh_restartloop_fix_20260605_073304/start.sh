#!/bin/sh
set -eu

# QFOS_START_DB_CONTRACT
export DB_PATH="${DB_PATH:-/app/data/quant.db}"
export DATABASE_PATH="${DATABASE_PATH:-/app/data/quant.db}"
export SQLITE_DB_PATH="${SQLITE_DB_PATH:-/app/data/quant.db}"
export QFOS_DB_PATH="${QFOS_DB_PATH:-/app/data/quant.db}"
export QUANT_DB_PATH="${QUANT_DB_PATH:-/app/data/quant.db}"
export DATABASE_URL="${DATABASE_URL:-sqlite:////app/data/quant.db}"
export QFOS_SQLITE_TIMEOUT="${QFOS_SQLITE_TIMEOUT:-30}"

mkdir -p /app/data
touch /app/data/quant.db
chmod 666 /app/data/quant.db || true
chmod 777 /app/data || true

python - <<'PY'
import os, sqlite3
p = "/app/data/quant.db"
os.makedirs(os.path.dirname(p), exist_ok=True)
con = sqlite3.connect(p, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
con.execute("select 1")
con.close()
print("[STARTUP_DB_INIT] DB_OK /app/data/quant.db", flush=True)
PY

#!/bin/bash
set -e

export DB_PATH="${DB_PATH:-/app/data/quant.db}"
export DATABASE_PATH="${DATABASE_PATH:-$DB_PATH}"
export SQLITE_DB_PATH="${SQLITE_DB_PATH:-$DB_PATH}"
export QFOS_DB_PATH="${QFOS_DB_PATH:-$DB_PATH}"
export QUANT_DB_PATH="${QUANT_DB_PATH:-$DB_PATH}"
export DATABASE_URL="${DATABASE_URL:-sqlite:////app/data/quant.db}"

mkdir -p /app/data

python - <<'PY'
import os, sqlite3, traceback

db = (
    os.environ.get("DB_PATH")
    or os.environ.get("DATABASE_PATH")
    or os.environ.get("SQLITE_DB_PATH")
    or os.environ.get("QFOS_DB_PATH")
    or os.environ.get("QUANT_DB_PATH")
    or "/app/data/quant.db"
)

for k in ["DB_PATH", "DATABASE_PATH", "SQLITE_DB_PATH", "QFOS_DB_PATH", "QUANT_DB_PATH"]:
    os.environ[k] = db
os.environ["DATABASE_URL"] = "sqlite:///" + db if db.startswith("/") else "sqlite:///" + os.path.abspath(db)

parent = os.path.dirname(os.path.abspath(db))
os.makedirs(parent, exist_ok=True)

print("[STARTUP_DB_INIT] db=", db)
print("[STARTUP_DB_INIT] parent=", parent, "exists=", os.path.exists(parent), "writable=", os.access(parent, os.W_OK))

try:
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS db_probe(id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO db_probe DEFAULT VALUES")
    con.commit()
    con.close()
    print("[STARTUP_DB_INIT] DB_OK")
except Exception as e:
    print("[STARTUP_DB_INIT] DB_FAIL", repr(e))
    traceback.print_exc()
    raise
PY

exec python main.py
