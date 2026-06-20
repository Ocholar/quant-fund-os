from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")

# 1) Remove unsafe repair logic that marks warming features ready at history_len >= 4.
old = '''        if f.get("ready") is not True:
            try:
                history_len = int(float(f.get("history_len", 0) or 0))
            except Exception:
                history_len = 0
            if history_len >= 4:
                f["ready"] = True

'''

if old in text:
    text = text.replace(old, '''        # Do not override FeatureStore readiness.
        # WARMING_UP / insufficient_history must remain not ready.
        if str(f.get("symbol_regime", "")).upper() == "WARMING_UP":
            f["ready"] = False

''')
    print("Removed unsafe history_len>=4 ready override.")
else:
    print("Exact unsafe ready override block not found; applying regex fallback.")
    text = re.sub(
        r'''        if f\.get\("ready"\) is not True:\n            try:\n                history_len = int\(float\(f\.get\("history_len", 0\) or 0\)\)\n            except Exception:\n                history_len = 0\n            if history_len >= 4:\n                f\["ready"\] = True\n\n''',
        '''        # Do not override FeatureStore readiness.
        # WARMING_UP / insufficient_history must remain not ready.
        if str(f.get("symbol_regime", "")).upper() == "WARMING_UP":
            f["ready"] = False

''',
        text,
        count=1,
    )

# 2) Harden ready-normal validator: WARMING_UP can never count as ready NORMAL.
needle = '''    if str(feature.get("source", "")).upper() != "NORMAL":
        return False
'''

replacement = '''    if str(feature.get("source", "")).upper() != "NORMAL":
        return False
    if str(feature.get("symbol_regime", "")).upper() == "WARMING_UP":
        return False
'''

if replacement not in text:
    text = text.replace(needle, replacement, 1)
    print("Added WARMING_UP rejection to ready-normal validator.")
else:
    print("WARMING_UP rejection already present.")

# 3) Harden contract repair: WARMING_UP cannot remain ready after repair.
repair_marker = '''        if not f.get("symbol_regime"):
            f["symbol_regime"] = "SYMBOL_NEUTRAL"
'''

repair_replacement = '''        if not f.get("symbol_regime"):
            f["symbol_regime"] = "SYMBOL_NEUTRAL"

        if str(f.get("symbol_regime", "")).upper() == "WARMING_UP":
            f["ready"] = False
'''

if repair_replacement not in text:
    text = text.replace(repair_marker, repair_replacement, 1)
    print("Added WARMING_UP ready=False guard in contract repair.")
else:
    print("WARMING_UP repair guard already present.")

path.write_text(text, encoding="utf-8")
