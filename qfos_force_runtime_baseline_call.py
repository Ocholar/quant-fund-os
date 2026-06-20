from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

required_marker = "QFOS_AGENT5_RUNTIME_BASELINE_SYNC_V1"
func_name = "qfos_force_runtime_clean_baseline_if_db_clean"

if required_marker not in text:
    raise SystemExit("ERROR: runtime baseline sync helper missing")

# Remove any previous accidental standalone duplicate call comments not needed.
# Then insert an explicit call immediately after the existing qfos_clean_runtime_state_if_db_baseline() startup call.
lines = text.splitlines()
out = []
inserted = False

for i, line in enumerate(lines):
    out.append(line)

    stripped = line.strip()

    # Only target the real top-level startup call, not comments or function definitions.
    if (
        stripped == "qfos_clean_runtime_state_if_db_baseline()"
        and not inserted
    ):
        indent = line[:len(line) - len(line.lstrip())]
        out.append(f"{indent}try:")
        out.append(f"{indent}    qfos_force_runtime_clean_baseline_if_db_clean()")
        out.append(f"{indent}except Exception as e:")
        out.append(f"{indent}    print(f'[QFOS_RUNTIME_BASELINE_SYNC_CALL_ERROR] error={{e}}', flush=True)")
        inserted = True

if not inserted:
    raise SystemExit("ERROR: could not find top-level qfos_clean_runtime_state_if_db_baseline() call")

new_text = "\n".join(out) + "\n"

# Verify we now have an actual call after the old guard.
if "qfos_force_runtime_clean_baseline_if_db_clean()" not in new_text:
    raise SystemExit("ERROR: call insertion failed")

path.write_text(new_text, encoding="utf-8")
print("PATCH_WRITE_OK")
