from pathlib import Path
import ast
import json

path = Path("main.py")
src = path.read_bytes().decode("utf-8-sig")
lines = src.splitlines()
tree = ast.parse(src)

targets = (
    "sideways_stagnation_exit",
    "sideways_take_profit_exit",
    "sideways_stop_loss_exit",
)

def block(start, end):
    start = max(1, start)
    end = min(len(lines), end)
    return "\n".join(
        f"{n:05d}: {lines[n - 1]}"
        for n in range(start, end + 1)
    )

functions = []

for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue

    node_end = getattr(node, "end_lineno", node.lineno)
    body = "\n".join(lines[node.lineno - 1:node_end]).lower()

    if any(term in body for term in targets):
        functions.append({
            "name": node.name,
            "start_line": node.lineno,
            "end_line": node_end,
            "context": block(node.lineno - 3, min(node_end, node.lineno + 95)),
        })

literal_hits = []

for number, line in enumerate(lines, start=1):
    lower = line.lower()
    if any(term in lower for term in targets):
        literal_hits.append({
            "line": number,
            "context": block(number - 8, number + 20),
        })

call_hits = []

for number, line in enumerate(lines, start=1):
    lower = line.lower()

    if (
        "qfos_persist_fill_atomic(" in lower
        or "apply_sell(" in lower
        or "_qfos_pe_sell(" in lower
        or "_qfos_poswd_close_position(" in lower
        or "_qfos_watchdog_close_worst_loser_once(" in lower
        or "qfos_exit_lifecycle_db_sells(" in lower
        or "generate_sells(" in lower
    ):
        call_hits.append({
            "line": number,
            "context": block(number - 6, number + 18),
        })

print(json.dumps({
    "functions_that_can_emit_sideways_exit_labels": functions,
    "all_sideways_label_literals": literal_hits,
    "sell_producer_and_persistence_calls": call_hits,
}, indent=2))