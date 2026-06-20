from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")
before = text

# ------------------------------------------------------------------
# 1. Capture the imported pause_reason function immediately after import.
# ------------------------------------------------------------------
import_line = "from core.control import is_paused, pause_bot, pause_reason"

if import_line not in text:
    raise SystemExit("PATCH_FAILED: expected core.control import line not found")

if "QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason" not in text:
    text = text.replace(
        import_line,
        import_line + "\nQFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason",
        1,
    )

# ------------------------------------------------------------------
# 2. Add a safe helper that can survive pause_reason global pollution.
# ------------------------------------------------------------------
helper = r'''
# QFOS_PAUSE_REASON_CALLABLE_GUARD_START
def qfos_safe_pause_reason_text():
    """
    Return pause reason text without relying on the global pause_reason name.

    The baseline reset code must never overwrite pause_reason(), but older
    patches may still have polluted globals()["pause_reason"] or
    core.control.pause_reason. This helper always falls back to the original
    imported callable captured at module import time.
    """
    try:
        import core.control as _control
        fn = getattr(_control, "pause_reason", None)
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass

    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            return str(fn() or "")
    except Exception:
        pass

    try:
        val = globals().get("pause_reason", "")
        if callable(val):
            return str(val() or "")
        return str(val or "")
    except Exception:
        return ""


def qfos_restore_pause_reason_callable():
    """
    Repair accidental overwrite of pause_reason in this module and core.control.
    """
    try:
        fn = globals().get("QFOS_ORIGINAL_PAUSE_REASON_FN")
        if callable(fn):
            globals()["pause_reason"] = fn
            try:
                import core.control as _control
                if not callable(getattr(_control, "pause_reason", None)):
                    setattr(_control, "pause_reason", fn)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False
# QFOS_PAUSE_REASON_CALLABLE_GUARD_END
'''

if "# QFOS_PAUSE_REASON_CALLABLE_GUARD_START" not in text:
    insert_after = "QFOS_ORIGINAL_PAUSE_REASON_FN = pause_reason"
    text = text.replace(insert_after, insert_after + "\n\n" + helper.strip(), 1)

# ------------------------------------------------------------------
# 3. Replace direct pause_reason() calls with safe helper.
# ------------------------------------------------------------------
text = text.replace("pause_reason()", "qfos_safe_pause_reason_text()")

# But the replacement above also changes the captured helper fallback if run twice.
# Normalize accidental nested replacements.
text = text.replace("qfos_safe_qfos_safe_pause_reason_text_text()", "qfos_safe_pause_reason_text()")
text = text.replace("qfos_safe_pause_reason_text() if callable(pause_reason)", "qfos_safe_pause_reason_text() if callable(pause_reason)")

# ------------------------------------------------------------------
# 4. Never assign empty string to global pause_reason.
# ------------------------------------------------------------------
text = text.replace('globals()["pause_reason"] = ""', 'globals()["pause_reason_value"] = ""')
text = text.replace("globals()['pause_reason'] = ''", "globals()['pause_reason_value'] = ''")

# Also protect plain assignments to pause_reason = "".
text = re.sub(
    r"(?m)^(\s*)pause_reason\s*=\s*['\"]{2}\s*$",
    r"\1pause_reason_value = ''",
    text,
)

# ------------------------------------------------------------------
# 5. Prevent core.control.pause_reason from being overwritten by reset helpers.
#    Replace dangerous setattr(_control, name, "") with callable-safe version.
# ------------------------------------------------------------------
text = text.replace(
'''                try:
                    setattr(_control, name, "")
                except Exception:
                    pass''',
'''                try:
                    current_attr = getattr(_control, name, None)
                    if name == "pause_reason" and callable(current_attr):
                        pass
                    else:
                        setattr(_control, name, "")
                except Exception:
                    pass'''
)

# ------------------------------------------------------------------
# 6. Ensure reset functions repair callable after any cleanup.
# ------------------------------------------------------------------
for anchor in [
    "qfos_baseline_authority_clear_redis_control_state()",
    "qfos_baseline_authority_clear_control_module_state()",
    "qfos_clear_stale_runtime_pause_state(source=source)",
    "globals()[\"paused\"] = False",
]:
    if anchor in text:
        text = text.replace(
            anchor,
            anchor + "\n        try:\n            qfos_restore_pause_reason_callable()\n        except Exception:\n            pass",
            1,
        )

# ------------------------------------------------------------------
# 7. Make live payload use safe pause reason text.
# ------------------------------------------------------------------
text = text.replace(
    "pause_reason_value = qfos_safe_pause_reason_text() if callable(pause_reason) else str(pause_reason or '')",
    "pause_reason_value = qfos_safe_pause_reason_text()",
)

text = text.replace(
    "'pause_reason': pause_reason,",
    "'pause_reason': pause_reason_value,",
)

# If payload now contains safe helper directly, fine. Keep it explicit.
if "pause_reason_value = qfos_safe_pause_reason_text()" not in text:
    needle = "            live_payload = {'name': 'Quant Fund OS'"
    if needle in text:
        text = text.replace(
            needle,
            "            pause_reason_value = qfos_safe_pause_reason_text()\n" + needle,
            1,
        )

# ------------------------------------------------------------------
# 8. Add runtime assertion before main loop diagnostics area if possible.
# ------------------------------------------------------------------
if "QFOS_PAUSE_REASON_CALLABLE_ASSERT" not in text:
    assert_block = '''
# QFOS_PAUSE_REASON_CALLABLE_ASSERT
try:
    qfos_restore_pause_reason_callable()
except Exception:
    pass
'''
    marker = "def main():"
    if marker in text:
        text = text.replace(marker, assert_block + "\n" + marker, 1)

path.write_text(text, encoding="utf-8")

print("PAUSE_REASON_CALLABLE_GUARD_PATCH_OK")
print("CHANGED", text != before)
