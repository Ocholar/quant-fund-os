from pathlib import Path
import ast

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

marker = "# QFOS_DISABLE_RESCUE_BUYS_V2"

if marker in src:
    print("RESCUE_BLOCK_V2_ALREADY_PRESENT")
    raise SystemExit(0)

try:
    tree = ast.parse(src)
except SyntaxError as exc:
    raise SystemExit(f"PATCH_FAILED: main.py is not parseable: {exc}")

targets = [
    node
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "qfos_persist_fill_atomic"
]

if not targets:
    raise SystemExit(
        "PATCH_FAILED: qfos_persist_fill_atomic definition not found. "
        "No source changed."
    )

target = max(targets, key=lambda node: node.lineno)

if not target.body:
    raise SystemExit(
        "PATCH_FAILED: final qfos_persist_fill_atomic has no function body."
    )

lines = src.splitlines(keepends=True)
first_body_line_index = target.body[0].lineno - 1
first_body_line = lines[first_body_line_index]
indent = first_body_line[: len(first_body_line) - len(first_body_line.lstrip())]

patch_lines = [
    f"{indent}# QFOS_DISABLE_RESCUE_BUYS_V2\n",
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
    f"{indent}    _qfos_is_rescue = (\n",
    f"{indent}        'allocator_rescue' in _qfos_rescue_strategy\n",
    f"{indent}        or 'allocator_rescue' in _qfos_rescue_fill_source\n",
    f"{indent}        or 'allocator_rescue' in _qfos_rescue_call_source\n",
    f"{indent}    )\n",
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
    f"{indent}    print(\n",
    f"{indent}        f'[RESCUE_BUY_BLOCK_GUARD_ERROR] error={{_qfos_rescue_guard_error!r}}',\n",
    f"{indent}        flush=True,\n",
    f"{indent}    )\n",
]

lines[first_body_line_index:first_body_line_index] = patch_lines
path.write_text("".join(lines), encoding="utf-8")

parameters = [arg.arg for arg in target.args.args]

print(
    "RESCUE_BLOCK_V2_PATCH_OK "
    f"function_line={target.lineno} "
    f"inserted_before_line={target.body[0].lineno} "
    f"parameters={parameters}"
)