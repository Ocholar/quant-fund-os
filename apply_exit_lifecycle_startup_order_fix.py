from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "QFOS_AGENT5_EXIT_STARTUP_ORDER_FIX_V1"
if marker in text:
    print("PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

old = '''try:
    qfos_exit_lifecycle_ensure_tables()
    qfos_exit_lifecycle_evaluate_once(source="startup_once")
    qfos_exit_lifecycle_start_daemon()
    print("[EXIT_LIFECYCLE] startup_call_installed_and_executed", flush=True)
except Exception as e:
    print(f"[EXIT_DECISION_ERROR] startup_call_v1 error={e}", flush=True)'''

new = '''# QFOS_AGENT5_EXIT_STARTUP_ORDER_FIX_V1
# Do not evaluate exits during module load. The actual atomic persistence
# function is defined later in this file. The daemon itself already waits
# fail-closed until qfos_persist_fill_atomic is callable.
try:
    qfos_exit_lifecycle_ensure_tables()
    qfos_exit_lifecycle_start_daemon()
    print("[EXIT_LIFECYCLE] startup_daemon_registered_no_early_evaluation", flush=True)
except Exception as e:
    print(f"[EXIT_DECISION_ERROR] startup_call_v1 error={e}", flush=True)'''

if old not in text:
    raise SystemExit("ERROR: unsafe startup_once block not found")

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PATCH_WRITE_OK")
