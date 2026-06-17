#!/bin/bash
set -e

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://qfos:qfos_password@postgres:5432/quant_fund_os}"

# Wait for postgres to be ready and initialize db_probe table
python - <<'PY'
import os
import time
from sqlalchemy import create_engine, text

db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgresql"):
    engine = create_engine(db_url)
    max_retries = 30
    for i in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                
                if os.path.exists('schema.sql'):
                    with open('schema.sql', 'r') as f:
                        schema = f.read()
                        for stmt in schema.split(';'):
                            stmt = stmt.strip()
                            if stmt:
                                conn.execute(text(stmt))
                                
                conn.commit()
            print("[STARTUP_DB_INIT] DB_OK (PostgreSQL)")
            break
        except Exception as e:
            if i < max_retries - 1:
                print(f"[STARTUP_DB_INIT] Waiting for PostgreSQL... ({i+1}/{max_retries})")
                time.sleep(2)
            else:
                print("[STARTUP_DB_INIT] DB_FAIL", repr(e))
                raise
else:
    print("[STARTUP_DB_INIT] Not using postgresql, skipping pg init.")
PY

python -m uvicorn services.api:app --host 0.0.0.0 --port 8080 &
python main.py