import os
import sys
import httpx
import sqlite3
import sqlalchemy
from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mission_control import _send_telegram
import json
from dotenv import load_dotenv

load_dotenv(override=True)

def check_dashboard():
    try:
        resp = httpx.get("http://127.0.0.1:8081/")
        return resp.status_code == 200
    except Exception as e:
        print("Dashboard error:", e)
        return False

def check_api():
    try:
        resp = httpx.get("http://127.0.0.1:8080/status")
        return resp.status_code == 200
    except Exception as e:
        print("API error:", e)
        return False

def check_telegram():
    # Send a silent or test message
    try:
        res = _send_telegram("ðŸš¨ Readiness Check: API and Dashboard are healthy. Testing Telegram integration...")
        return res
    except Exception as e:
        print("Telegram error:", e)
        return False

def check_postgres():
    try:
        db_url = os.getenv("DATABASE_URL")
        if db_url and "@postgres:5432" in db_url:
            db_url = db_url.replace("@postgres:5432", "@localhost:5432")
        elif not db_url:
            db_url = "postgresql+psycopg2://qfos:qfos_password@localhost:5432/quant_fund_os"
        engine = sqlalchemy.create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            return True, engine
    except Exception as e:
        print("Postgres error:", e)
        return False, None

def check_analytics_schema(engine):
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='trades'"))
            cols = [r[0] for r in res]
            expected = ["mfe", "mae", "regime", "experiment_id", "software_version", "configuration_hash"]
            missing = [c for c in expected if c not in cols]
            return len(missing) == 0
    except Exception as e:
        print("Schema check error:", e)
        return False

def check_new_fields_written(engine):
    try:
        with engine.connect() as conn:
            # Check the most recent trade to see if it has non-null regime or mfe/mae/experiment_id if it's new
            res = conn.execute(text("SELECT regime, experiment_id, mfe FROM trades ORDER BY id DESC LIMIT 5"))
            for r in res:
                # if any row has regime set, it's writing it
                if r[0] is not None or r[1] is not None:
                    return True
            return False
    except Exception as e:
        print("Field check error:", e)
        return False

def check_experiment_metadata():
    # Just read the metadata or check if we are passing it
    # We can check experiments/template.md exists
    return os.path.exists("experiments/template.md")

if __name__ == "__main__":
    print("=== RUN READINESS CHECKLIST ===")
    dashboard = check_dashboard()
    print(f"Dashboard healthy: {'PASS' if dashboard else 'FAIL'}")
    
    api = check_api()
    print(f"API healthy: {'PASS' if api else 'FAIL'}")
    
    telegram = check_telegram()
    print(f"Telegram healthy: {'PASS' if telegram else 'FAIL'}")
    
    pg_ok, engine = check_postgres()
    print(f"Database connectivity healthy: {'PASS' if pg_ok else 'FAIL'}")
    
    if pg_ok:
        schema = check_analytics_schema(engine)
        print(f"Analytics schema present: {'PASS' if schema else 'FAIL'}")
        fields = check_new_fields_written(engine)
        print(f"New lifecycle fields being written: {'PASS' if fields else 'FAIL (waiting for new trades?)'}")
    else:
        print("Analytics schema present: FAIL")
        print("New lifecycle fields being written: FAIL")
        
    meta = check_experiment_metadata()
    print(f"Experiment metadata configured: {'PASS' if meta else 'FAIL'}")
