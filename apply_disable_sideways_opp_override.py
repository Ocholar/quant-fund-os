from pathlib import Path
import ast

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")

marker = "# QFOS_DISABLE_SIDEWAYS_OPP_OVERRIDE_V1"

if marker in src:
    print("SIDEWAYS_OPP_OVERRIDE_ALREADY_DISABLED")
    raise SystemExit(0)

tree = ast.parse(src)

targets = [
    node
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "_qfos_opp_can_override_entry_reject"
]

if len(targets) != 1:
    raise SystemExit(
        "PATCH_FAILED: expected one _qfos_opp_can_override_entry_reject "
        f"definition, found {len(targets)}."
    )

target = targets[0]

if len(target.body) < 1:
    raise SystemExit("PATCH_FAILED: opportunity override function has no body.")

# Find the opening enabled-check:
# if not QFOS_OPPORTUNITY_MODE_ENABLED:
#     return False
enabled_check = None

for node in target.body:
    if isinstance(node, ast.If):
        test_text = ast.unparse(node.test)
        if "QFOS_OPPORTUNITY_MODE_ENABLED" in test_text:
            enabled_check = node
            break

if enabled_check is None:
    raise SystemExit(
        "PATCH_FAILED: enabled-check anchor not found in opportunity override."
    )

insert_line_index = enabled_check.end_lineno

lines = src.splitlines(keepends=True)

first_function_body_line = lines[target.body[0].lineno - 1]
indent = first_function_body_line[
    :len(first_function_body_line) - len(first_function_body_line.lstrip())
]

patch_lines = [
    f"{indent}{marker}\n",
    f"{indent}# SIDEWAYS must not revive a rejected evo_* entry through\n",
    f"{indent}# low-exposure Opportunity Mode. Normal approved entries remain\n",
    f"{indent}# eligible; only the rejected-entry override is disabled.\n",
    f"{indent}try:\n",
    f"{indent}    _qfos_opp_regime = str(((_qfos_opp_state() or {{}}).get('regime')) or '').upper()\n",
    f"{indent}    if _qfos_opp_regime == 'SIDEWAYS':\n",
    f"{indent}        print(\n",
    f"{indent}            '[OPPORTUNITY_MODE_OVERRIDE_BLOCK] '\n",
    f"{indent}            f'symbol={{symbol}} strategy={{strategy}} '\n",
    f"{indent}            f'reason={{reason}} regime=SIDEWAYS',\n",
    f"{indent}            flush=True,\n",
    f"{indent}        )\n",
    f"{indent}        return False\n",
    f"{indent}except Exception as _qfos_opp_sideways_guard_error:\n",
    f"{indent}    print(\n",
    f"{indent}        '[OPPORTUNITY_MODE_OVERRIDE_BLOCK] '\n",
    f"{indent}        f'reason=regime_lookup_error '\n",
    f"{indent}        f'error={{_qfos_opp_sideways_guard_error!r}}',\n",
    f"{indent}        flush=True,\n",
    f"{indent}    )\n",
    f"{indent}    return False\n",
    "\n",
]

lines[insert_line_index:insert_line_index] = patch_lines
patched = "".join(lines)

ast.parse(patched)
path.write_bytes(patched.encode("utf-8"))

print(
    "SIDEWAYS_OPP_OVERRIDE_PATCH_OK "
    f"function_line={target.lineno} "
    f"inserted_after_line={enabled_check.end_lineno}"
)