from pathlib import Path
import re
import ast

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")

marker = "# QFOS_RESCUE_HOOK_CONSUME_ON_BLOCK_V1"

if marker in src:
    print("RESCUE_HOOK_CONSUME_PATCH_ALREADY_PRESENT")
    raise SystemExit(0)

hook_anchor = 'qfos_exec_bridge_process_orders(orders, source="allocator_rescue_hook")'
hook_index = src.rfind(hook_anchor)

if hook_index < 0:
    raise SystemExit(
        "PATCH_FAILED: active allocator_rescue_hook execution-bridge call not found."
    )

segment_end = src.find("\n    else:", hook_index)

if segment_end < 0:
    segment_end = min(len(src), hook_index + 1800)

segment = src[hook_index:segment_end]

pattern = re.compile(
    r"(?m)^(?P<indent>[ \t]*)if _qfos_exec_bridge_applied > 0:\r?\n"
    r"(?P=indent)[ \t]+orders = \[\]"
)

matches = list(pattern.finditer(segment))

if len(matches) != 1:
    raise SystemExit(
        "PATCH_FAILED: expected exactly one conditional rescue-hook "
        f"orders-clear block, found {len(matches)}."
    )

match = matches[0]
indent = match.group("indent")

replacement = (
    f"{indent}{marker}\n"
    f"{indent}# The rescue hook owns these orders. Never return them to the\n"
    f"{indent}# main-loop path after bridge allow/reject, otherwise a blocked\n"
    f"{indent}# rescue BUY can be persisted later as a normal evo_* BUY.\n"
    f"{indent}orders = []\n"
    f"{indent}print(\n"
    f"{indent}    f'[RESCUE_HOOK_CONSUMED] '\n"
    f"{indent}    f'bridge_applied={{_qfos_exec_bridge_applied}} '\n"
    f"{indent}    'returned_orders=0',\n"
    f"{indent}    flush=True,\n"
    f"{indent})"
)

patched_segment = (
    segment[:match.start()]
    + replacement
    + segment[match.end():]
)

patched = src[:hook_index] + patched_segment + src[segment_end:]

try:
    ast.parse(patched)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: patched main.py would not parse: {exc}")

path.write_bytes(patched.encode("utf-8"))

print(
    "RESCUE_HOOK_CONSUME_PATCH_OK "
    f"hook_line={src[:hook_index].count(chr(10)) + 1}"
)