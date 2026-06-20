from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

before = text

# The imported pause_reason from core.control is a function.
# Do not declare it global and do not assign a string to it.
# This removes bad global declarations added by the previous broad patch.
text = re.sub(r"(?m)^\s*global pause_reason\s*\n", "", text)

# Fix the exact bot-loop clean-baseline assignment.
# Old:
#     pause_reason = ''
# New:
#     pause_reason_value = ''
text = text.replace(
    "                pause_reason = ''\n                rejected = []",
    "                pause_reason_value = ''\n                rejected = []",
)

# Ensure pause_reason_value is defined before live_payload in the normal path.
needle = "            live_payload = {'name': 'Quant Fund OS'"
if needle in text and "pause_reason_value = pause_reason() if callable(pause_reason) else str(pause_reason or '')" not in text:
    text = text.replace(
        needle,
        "            try:\n"
        "                pause_reason_value = pause_reason() if callable(pause_reason) else str(pause_reason or '')\n"
        "            except Exception:\n"
        "                pause_reason_value = ''\n"
        "            live_payload = {'name': 'Quant Fund OS'",
        1,
    )

# Replace the payload field to use the safe value, not the function object.
text = text.replace(
    "'pause_reason': pause_reason,",
    "'pause_reason': pause_reason_value,",
)

# If any accidental assignment to pause_reason = '' remains, rewrite it
# unless it is inside a string/comment. This targets common exact indentation forms.
text = re.sub(
    r"(?m)^(\s*)pause_reason\s*=\s*['\"]{2}\s*$",
    r"\1pause_reason_value = ''",
    text,
)

# Fix baseline snapshot created_at/updated_at when helper is unavailable.
# Existing failure:
# created_at None violates NOT NULL constraint.
text = text.replace(
    '''            if "created_at" in cols:
                values["created_at"] = _now_iso_local() if "_now_iso_local" in globals() else None
            if "updated_at" in cols:
                values["updated_at"] = _now_iso_local() if "_now_iso_local" in globals() else None''',
    '''            if "created_at" in cols:
                try:
                    values["created_at"] = _now_iso_local() if "_now_iso_local" in globals() else datetime.utcnow().isoformat()
                except Exception:
                    values["created_at"] = datetime.utcnow().isoformat()
            if "updated_at" in cols:
                try:
                    values["updated_at"] = _now_iso_local() if "_now_iso_local" in globals() else datetime.utcnow().isoformat()
                except Exception:
                    values["updated_at"] = datetime.utcnow().isoformat()'''
)

# Ensure datetime import exists for the snapshot fallback.
if "from datetime import" in text:
    if "datetime" not in re.search(r"from datetime import ([^\n]+)", text).group(1):
        text = re.sub(
            r"from datetime import ([^\n]+)",
            r"from datetime import \1, datetime",
            text,
            count=1,
        )
elif "import datetime" not in text:
    text = "from datetime import datetime\n" + text

path.write_text(text, encoding="utf-8")

print("PAUSE_REASON_CALLABLE_FIX_OK")
print("CHANGED", text != before)
