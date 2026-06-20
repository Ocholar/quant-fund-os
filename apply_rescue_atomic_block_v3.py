from pathlib import Path
import ast

path = Path("main.py")
raw = path.read_bytes()

had_bom = raw.startswith(b"\xef\xbb\xbf")
src = raw.decode("utf-8-sig")

marker = "# QFOS_DISABLE_RESCUE_BUYS_V3"

if marker in src:
    print("RESCUE_BLOCK_V3_ALREADY_PRESENT")
    raise SystemExit(0)

try:
    tree = ast.parse(src)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: main.py is not parseable after BOM-safe decode: {exc}")

# Prefer module-level definitions because the final module-level binding is
# what runtime callers resolve. Fall back to nested definitions only if needed.
targets = [
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "qfos_persist_fill_atomic"
]

scope = "module"

if not targets:
    targets = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "qfos_persist_fill_atomic"
    ]
    scope = "fallback_nested"

if not targets:
    raise SystemExit(
        "PATCH_FAILED: qfos_persist_fill_atomic definition not found. "
        "No source changed."
    )

target = max(targets, key=lambda node: node.lineno)

if not target.body:
    raise SystemExit(
        "PATCH_FAILED: selected qfos_persist_fill_atomic has no body."
    )

# Preserve a real function docstring by inserting after it.
body_index = 0

if (
    isinstance(target.body[0], ast.Expr)
    and isinstance(getattr(target.body[0], "value", None), ast.Constant)
    and isinstance(target.body[0].value.value, str)
):
    body_index = 1

if body_index >= len(target.body):
    raise SystemExit(
        "PATCH_FAILED: selected function contains only a docstring."
    )

lines = src.splitlines(keepends=True)
insert_at = target.body[body_index].lineno - 1

first_body_line = lines[insert_at]
indent = first_body_line[:len(first_body_line) - len(first_body_line.lstrip())]

patch_lines = [
    f"{indent}# QFOS_DISABLE_RESCUE_BUYS_V3\n",
    f"{indent}# Final persistence containment for rescue-originated BUYs.\n",
    f"{indent}# SELLs remain permitted. Non-rescue BUY paths remain unchanged.\n",
    f"{indent}try:\n",
    f"{indent}    _qfos_rescue_fill = fill if isinstance(fill, dict) else {{\n",
    f"{indent}        'side': getattr(fill, 'side', None),\n",
    f"{indent}        'symbol': getattr(fill, 'symbol', None),\n",
    f"{indent}        'strategy': getattr(fill, 'strategy', None),\n",
    f"{indent}        'source': getattr(fill, 'source', None),\n",
    f"{indent}    }}\n",
    f"{indent}    _qfos_rescue_side = str(_qfos_rescue_fill.get('side') or '').strip().lower()\n",
    f"{indent}    _qfos_rescue_strategy = str(_qfos_rescue_fill.get('strategy') or '').strip().lower()\n",
    f"{indent}    _qfos_rescue_fill_source = str(_qfos_rescue_fill.get('source') or '').strip().lower()\n",
    f"{indent}    _qfos_rescue_call_source = str(locals().get('source', '') or '').strip().lower()\n",
    f"{indent}    _qfos_rescue_context = ' '.join((\n",
    f"{indent}        _qfos_rescue_strategy,\n",
    f"{indent}        _qfos_rescue_fill_source,\n",
    f"{indent}        _qfos_rescue_call_source,\n",
    f"{indent}    ))\n",
    f"{indent}    _qfos_is_rescue = 'allocator_rescue' in _qfos_rescue_context\n",
    f"{indent}    if _qfos_rescue_side == 'buy' and _qfos_is_rescue:\n",
    f"{indent}        print(\n",
    f"{indent}            '[RESCUE_BUY_BLOCK] '\n",
    f"{indent}            f\"symbol={{_qfos_rescue_fill.get('symbol')}} \"\n",
    f"{indent}            f\"strategy={{_qfos_rescue_fill.get('strategy')}} \"\n",
    f"{indent}            f\"source={{locals().get('source', '')}} \"\n",
    f"{indent}            'reason=negative_raw_fill_expectancy',\n",
    f"{indent}            flush=True,\n",
    f"{indent}        )\n",
    f"{indent}        return False\n",
    f"{indent}except Exception as _qfos_rescue_guard_error:\n",
    f"{indent}    _qfos_rescue_fallback_text = repr(fill).lower()\n",
    f"{indent}    if 'allocator_rescue' in _qfos_rescue_fallback_text:\n",
    f"{indent}        print(\n",
    f"{indent}            f'[RESCUE_BUY_BLOCK_GUARD_ERROR] error={{_qfos_rescue_guard_error!r}}',\n",
    f"{indent}            flush=True,\n",
    f"{indent}        )\n",
    f"{indent}        return False\n",
]

lines[insert_at:insert_at] = patch_lines
patched = "".join(lines)

# Ensure the patched output itself parses before writing.
ast.parse(patched)

# Write UTF-8 without BOM. Python and Docker both handle this consistently.
path.write_bytes(patched.encode("utf-8"))

parameters = [arg.arg for arg in target.args.args]

print(
    "RESCUE_BLOCK_V3_PATCH_OK "
    f"had_bom={had_bom} "
    f"scope={scope} "
    f"function_line={target.lineno} "
    f"inserted_before_line={target.body[body_index].lineno} "
    f"parameters={parameters}"
)