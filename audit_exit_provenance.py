from pathlib import Path
import ast
import json

path = Path("main.py")
src = path.read_bytes().decode("utf-8-sig")
lines = src.splitlines()
tree = ast.parse(src)

EXIT_TERMS = (
    "sideways_stagnation_exit",
    "sideways_take_profit_exit",
    "sideways_stop_loss_exit",
    "sideways_max_hold_exit",
    "sideways_stale_negative_exit",
)

def context(line_no, before=10, after=30):
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
        body_text = "\n".join(
            lines[node.lineno - 1 : getattr(node, "end_lineno", node.lineno)]
        ).lower()

        if (
            any(term in body_text for term in EXIT_TERMS)
            or "generate_sells" in node.name.lower()
            or "profit_engine" in node.name.lower()
            or "watchdog" in node.name.lower()
            or "exit_lifecycle" in node.name.lower()
        ):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "args": [arg.arg for arg in node.args.args],
                "context": context(node.lineno, 4, 70),
            })

term_hits = {}
for term in EXIT_TERMS:
    term_hits[term] = [
        context(i, 8, 24)
        for i, line in enumerate(lines, start=1)
        if term in line.lower()
    ]

call_hits = []
for i, line in enumerate(lines, start=1):
    lower = line.lower()
    if (
        "generate_sells(" in lower
        or "qfos_exit_lifecycle_evaluate_once(" in lower
        or "_qfos_poswd_close_position(" in lower
        or "profit_engine" in lower
        or "active_position_watchdog" in lower
    ):
        call_hits.append(context(i, 6, 22))

print(json.dumps({
    "line_count": len(lines),
    "functions": functions,
    "term_hits": term_hits,
    "call_hits": call_hits,
}, indent=2))