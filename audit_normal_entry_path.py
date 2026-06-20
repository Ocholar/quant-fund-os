from pathlib import Path
import ast
import json

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")
lines = src.splitlines()

tree = ast.parse(src)

targets = [
    "qfos_expectancy_cycle_guard",
    "qfos_exec_bridge_process_orders",
    "qfos_exec_bridge_persist_fill",
    "qfos_persist_fill_atomic",
]

keywords = [
    "sideways_stagnation_exit",
    "sideways_take_profit_exit",
    "sideways_max_hold_exit",
    "sideways_stop_loss_exit",
    "can_buy",
    "apply_buy",
    "ENTRY QUALITY TOP",
    "entry_quality",
    "OPPORTUNITY_MODE",
    "final_applied_fills",
]

def block(line_no, before=8, after=24):
    start = max(1, line_no - before)
    end = min(len(lines), line_no + after)
    return {
        "start_line": start,
        "end_line": end,
        "text": "\n".join(
            f"{n:05d}: {lines[n - 1]}"
            for n in range(start, end + 1)
        ),
    }

functions = []

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name in targets:
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "args": [arg.arg for arg in node.args.args],
                "context": block(node.lineno, 3, 70),
            })

hits = {}

for keyword in keywords:
    found = []

    for number, line in enumerate(lines, start=1):
        if keyword.lower() in line.lower():
            found.append(block(number, 5, 16))

    hits[keyword] = found[:20]

print(json.dumps({
    "bom_present": raw.startswith(b"\xef\xbb\xbf"),
    "line_count": len(lines),
    "functions": functions,
    "keyword_hits": hits,
}, indent=2))