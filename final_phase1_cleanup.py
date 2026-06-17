from pathlib import Path
import re

p = Path("main.py")
s = p.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    re.S,
)

blocks = list(block_re.finditer(s))
if not blocks:
    raise SystemExit("FAIL: no QFOS atomic block found")

canonical = blocks[0].group(0)

# Remove all marked atomic blocks first.
s = block_re.sub("\n", s)

# Function remover by top-level def name.
def remove_top_level_func(src, name):
    pat = re.compile(rf"(?m)^def\s+{re.escape(name)}\s*\(")
    while True:
        m = pat.search(src)
        if not m:
            return src, False

        start = m.start()
        next_m = re.search(r"(?m)^(def|class)\s+\w+|^if\s+__name__\s*==", src[m.end():])
        if next_m:
            end = m.end() + next_m.start()
        else:
            end = len(src)

        src = src[:start].rstrip() + "\n\n" + src[end:].lstrip()
        return src, True

names = [
    "qfos_persist_fill_atomic",
    "_qfos_insert_trade_atomic",
    "_qfos_upsert_position_atomic",
    "_qfos_get_position_row",
    "_qfos_table_columns",
    "_qfos_first_existing_column",
    "_qfos_float",
    "_qfos_bool_int",
    "_qfos_log_atomic",
    "_qfos_now_utc_text",
    "_qfos_atomic_insert_trade",
    "_qfos_atomic_upsert_position",
    "_qfos_atomic_is_sqlalchemy",
    "_qfos_atomic_execute",
    "_qfos_atomic_fetchone",
    "_qfos_atomic_commit",
    "_qfos_atomic_rollback",
]

removed = []
changed = True
while changed:
    changed = False
    for n in names:
        s, did = remove_top_level_func(s, n)
        if did:
            removed.append(n)
            changed = True

# Remove leftover QFOS-only import/constant outside canonical block.
s = re.sub(r"(?m)^from datetime import datetime as _qfos_datetime\s*$\n?", "", s)
s = re.sub(r"(?m)^_QFOS_EPSILON\s*=\s*1e-12\s*$\n?", "", s)

# Reinsert exactly one canonical block before def main, else append.
m = re.search(r"(?m)^def\s+main\s*\(", s)
if m:
    s = s[:m.start()] + "\n" + canonical.strip() + "\n\n" + s[m.start():]
else:
    s = s.rstrip() + "\n\n" + canonical.strip() + "\n"

p.write_text(s, encoding="utf-8")

print("Removed duplicate/legacy functions:")
for n in sorted(set(removed)):
    print(" -", n)
print("DONE: exactly one canonical atomic block should remain.")
