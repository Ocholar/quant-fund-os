from pathlib import Path
import ast
import re

path = Path("main.py")

# utf-8-sig strips BOM / U+FEFF at the start of file.
text = path.read_text(encoding="utf-8-sig")

block_re = re.compile(
    r"# BEGIN QFOS_ATOMIC_FILL_PERSISTENCE_V1.*?# END QFOS_ATOMIC_FILL_PERSISTENCE_V1",
    flags=re.S,
)

blocks = list(block_re.finditer(text))
if not blocks:
    raise SystemExit("FAIL: no canonical QFOS atomic block found")

# Keep the first canonical block only.
canonical = blocks[0].group(0)

# Remove all canonical marker blocks from the source first.
text_without_blocks = block_re.sub("\n", text)

# Remove duplicate/legacy function definitions outside canonical block.
legacy_names = {
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
}

try:
    tree = ast.parse(text_without_blocks)
except SyntaxError as e:
    raise SystemExit(f"FAIL: cannot parse main.py after BOM handling: {e}")

lines = text_without_blocks.splitlines(keepends=True)
spans = []

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in legacy_names:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        spans.append((start, end, node.name))

for start, end, name in sorted(spans, reverse=True):
    del lines[start:end]

cleaned = "".join(lines)

# Remove duplicate constant/import lines outside canonical block if they exist.
cleaned = re.sub(r"(?m)^_QFOS_EPSILON\s*=\s*1e-12\s*\n", "", cleaned)
cleaned = re.sub(r"(?m)^from datetime import datetime as _qfos_datetime\s*\n", "", cleaned)

# Insert exactly one canonical block before def main(), otherwise append.
m = re.search(r"(?m)^def\s+main\s*\(", cleaned)
if m:
    final = cleaned[:m.start()] + "\n" + canonical + "\n\n" + cleaned[m.start():]
else:
    final = cleaned.rstrip() + "\n\n" + canonical + "\n"

# Write without BOM.
path.write_text(final, encoding="utf-8")

print("Removed duplicate/legacy helper functions:")
for name in sorted({name for _, _, name in spans}):
    print(" -", name)

print("Canonical QFOS atomic block count reset to 1.")
