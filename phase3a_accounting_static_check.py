from pathlib import Path
import re

s = Path("main.py").read_text(encoding="utf-8-sig")
m = re.search(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    s,
    flags=re.S,
)
if not m:
    raise SystemExit("FAIL: atomic block missing")

block = m.group(0)

required = [
    "def _qfos_exit_accounting_fields(",
    "def _qfos_assert_sell_exit_accounting(",
    '"is_exit": bool(exit_is_exit)',
    '"exit_reason": exit_reason',
    '"is_exit": _qfos_bool_int(normalized_fill.get("is_exit", False))',
    '"exit_reason": normalized_fill.get("exit_reason")',
]

for item in required:
    if item not in block:
        print("FAIL missing:", item)
        raise SystemExit(1)

print("PASS: Phase 3A accounting fields and guards exist")
