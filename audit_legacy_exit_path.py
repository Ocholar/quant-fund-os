from pathlib import Path
import ast
import json

path = Path("main.py")
src = path.read_bytes().decode("utf-8-sig")
lines = src.splitlines()
tree = ast.parse(src)

wanted_functions = {
    "_qfos_position_age_minutes",
    "_qfos_record_peak_change",
    "clear_position_exit_trackers",
    "_qfos_exit_decision",
    "generate_sells",
    "apply_sell",
}

def block(line_no, before=6, after=80):
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
        if node.name in wanted_functions:
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "context": block(node.lineno),
            })

needles = [
    "position_open_time",
    "position_peak_change",
    "clear_position_exit_trackers(",
    "_qfos_exit_decision =",
    "wrapped _qfos_exit_decision",
    "sideways_stagnation_exit",
    "sideways_take_profit_exit",
    "apply_sell(",
]

hits = {}

for needle in needles:
    found = []

    for number, line in enumerate(lines, start=1):
        if needle.lower() in line.lower():
            found.append(block(number, 5, 20))

    hits[needle] = found[:40]

print(json.dumps({
    "functions": functions,
    "hits": hits,
}, indent=2))