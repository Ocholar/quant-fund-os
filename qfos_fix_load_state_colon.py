from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

before = text

# Exact known failure:
# def load_state_from_db()
text = re.sub(
    r"(?m)^(\s*)def\s+load_state_from_db\s*\(\s*\)\s*$",
    r"\1def load_state_from_db():",
    text,
)

if text == before:
    print("NO_EXACT_LOAD_STATE_COLON_FIX_APPLIED")
else:
    print("LOAD_STATE_COLON_FIX_APPLIED")

path.write_text(text, encoding="utf-8")
