from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "# End QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_V1"
call_marker = "QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_START_CALL_V1"

if call_marker in text:
    print("START_CALL_ALREADY_PRESENT")
    raise SystemExit(0)

if marker not in text:
    raise SystemExit("ERROR: lifecycle end marker not found")

startup_call = r'''

# ============================================================
# QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_START_CALL_V1
# Purpose:
#   The lifecycle functions were installed, but the daemon was not
#   actually started because the previous patch confused the function
#   definition with a startup call.
# ============================================================

try:
    qfos_exit_lifecycle_ensure_tables()
    qfos_exit_lifecycle_evaluate_once(source="startup_once")
    qfos_exit_lifecycle_start_daemon()
    print("[EXIT_LIFECYCLE] startup_call_installed_and_executed", flush=True)
except Exception as e:
    print(f"[EXIT_DECISION_ERROR] startup_call_v1 error={e}", flush=True)

# ============================================================
# End QFOS_AGENT2_AGENT5_EXIT_LIFECYCLE_START_CALL_V1
# ============================================================
'''

text = text.replace(marker, marker + startup_call, 1)
path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
