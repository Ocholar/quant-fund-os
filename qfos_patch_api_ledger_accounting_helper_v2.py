from pathlib import Path

path = Path("services/api.py")
text = path.read_text(encoding="utf-8")

if "QFOS_AGENT5_API_LEDGER_ACCOUNTING_HELPER_V2" in text:
    print("API_PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

helper = r'''
# ============================================================
# QFOS_AGENT5_API_LEDGER_ACCOUNTING_HELPER_V2
# Helper only. Does not override routes. Existing routes can call
# this if they use DB engine/session globals. DB snapshot trigger is
# still the primary source-of-truth guard.
# ============================================================

def qfos_agent5_api_ledger_accounting_row(conn):
    try:
        return conn.execute(text("""
            SELECT *
            FROM qfos_current_ledger_accounting()
            LIMIT 1
        """)).mappings().first()
    except Exception:
        return None

# ============================================================
# End QFOS_AGENT5_API_LEDGER_ACCOUNTING_HELPER_V2
# ============================================================
'''

# Insert after imports if possible.
insert_after = "from sqlalchemy import text"
if insert_after in text:
    text = text.replace(insert_after, insert_after + "\n" + helper, 1)
else:
    text = helper + "\n" + text

path.write_text(text, encoding="utf-8")
print("API_PATCH_WRITE_OK")
