from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

original = text

# Known bad form:
# def load_state_from_db():
# qfos_apply_clean_ledger_runtime_reset(source='after_load_state_from_db'):
#     print('Recovering state from database...')
#
# Correct form:
# def load_state_from_db():
#     try:
#         qfos_apply_clean_ledger_runtime_reset(source='after_load_state_from_db')
#     except Exception as exc:
#         print(f"[BASELINE_AUTHORITY] after_load_state_reset_error={exc}", flush=True)
#     print('Recovering state from database...')

bad_pattern = re.compile(
    r"(?m)^def load_state_from_db\(\):\s*\n"
    r"qfos_apply_clean_ledger_runtime_reset\(source\s*=\s*['\"]after_load_state_from_db['\"]\):\s*\n"
    r"(\s*)print\(['\"]Recovering state from\s+database\.\.\.['\"]\)",
)

replacement = (
    "def load_state_from_db():\n"
    "    try:\n"
    "        qfos_apply_clean_ledger_runtime_reset(source='after_load_state_from_db')\n"
    "    except NameError:\n"
    "        # Baseline authority may be defined later during module load.\n"
    "        pass\n"
    "    except Exception as exc:\n"
    "        print(f\"[BASELINE_AUTHORITY] after_load_state_reset_error={exc}\", flush=True)\n"
    "    print('Recovering state from database...')"
)

text, count = bad_pattern.subn(replacement, text, count=1)

if count == 0:
    # More tolerant repair:
    lines = text.splitlines()
    out = []
    repaired = False
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.strip() == "def load_state_from_db():":
            out.append(line)

            if i + 1 < len(lines) and "qfos_apply_clean_ledger_runtime_reset" in lines[i + 1]:
                out.append("    try:")
                out.append("        qfos_apply_clean_ledger_runtime_reset(source='after_load_state_from_db')")
                out.append("    except NameError:")
                out.append("        # Baseline authority may be defined later during module load.")
                out.append("        pass")
                out.append("    except Exception as exc:")
                out.append("        print(f\"[BASELINE_AUTHORITY] after_load_state_reset_error={exc}\", flush=True)")
                i += 2
                repaired = True
                continue

        out.append(line)
        i += 1

    text = "\n".join(out) + "\n"

    if not repaired:
        raise SystemExit("LOAD_STATE_REPAIR_FAILED: malformed qfos_apply line not found after def load_state_from_db")

path.write_text(text, encoding="utf-8")

print("LOAD_STATE_REPAIR_OK")
