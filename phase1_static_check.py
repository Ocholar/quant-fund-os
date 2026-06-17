from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8")

def get_func(name):
    m = re.search(r"^def " + re.escape(name) + r"\([^\n]*\):\n", s, re.M)
    if not m:
        raise SystemExit(f"MISSING_FUNCTION {name}")
    m2 = re.search(r"^(def |class |if __name__|# =+)", s[m.end():], re.M)
    end = m.end() + m2.start() if m2 else len(s)
    return s[m.start():end]

for name in ["_qfos_pe_sell", "_qfos_poswd_close_position", "_qfos_watchdog_close_worst_loser_once"]:
    body = get_func(name)
    if "qfos_persist_fill_atomic" not in body:
        raise SystemExit(f"FAIL {name}: does not call qfos_persist_fill_atomic")
    if "INSERT INTO trades" in body:
        raise SystemExit(f"FAIL {name}: still contains direct INSERT INTO trades")
    print(f"PASS {name}: routed through qfos_persist_fill_atomic")

if "def qfos_persist_fill_atomic" not in s:
    raise SystemExit("FAIL: qfos_persist_fill_atomic missing")

if "for raw_fill in applied_fills:" not in s:
    raise SystemExit("FAIL: main applied_fills loop not patched")

print("STATIC_SAFETY_CHECK_OK")
