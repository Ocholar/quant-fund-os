import ast
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()

EXCLUDED_DIR_PREFIXES = (
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "agent5_", "joint_agent", "qfos_", "execution_logs"
)

EXCLUDED_FILES = {
    "inventory_execution_paths.py",
    "classify_execution_bypasses.py",
}

TARGET_FUNCTIONS = {
    "qfos_persist_fill_atomic",
    "qfos_exit_lifecycle_execute_sell",
    "qfos_exit_lifecycle_evaluate_once",
    "apply_buy",
    "apply_sell",
    "save_trade",
    "qfos_exec_bridge_process_orders",
    "qfos_exec_bridge_validate_fill",
    "_qfos_poswd_close_position",
    "_qfos_pe_sell",
    "_qfos_watchdog_close_worst_loser_once",
    "generate_sells",
}

SQL_MUTATORS = (
    "insert into trades",
    "update trades",
    "delete from trades",
    "insert into positions",
    "update positions",
    "delete from positions",
    "insert into portfolio_snapshots",
    "update portfolio_snapshots",
)

def is_excluded(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if path.name in EXCLUDED_FILES:
        return True
    return any(
        part.startswith(EXCLUDED_DIR_PREFIXES)
        for part in rel.parts
    )

def node_source(lines, node):
    start = max(0, node.lineno - 1)
    end = min(len(lines), getattr(node, "end_lineno", node.lineno))
    return "\n".join(lines[start:end])

def enclosing_function(node, parent_map):
    cur = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parent_map.get(id(cur))
    return "<module>"

items = []

for path in ROOT.rglob("*.py"):
    if is_excluded(path):
        continue

    try:
        src = path.read_text(encoding="utf-8-sig")
        lines = src.splitlines()
        tree = ast.parse(src)
    except Exception as exc:
        items.append({
            "file": str(path.relative_to(ROOT)),
            "type": "parse_error",
            "error": repr(exc),
        })
        continue

    parent_map = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent

    for node in ast.walk(tree):
        fn = enclosing_function(node, parent_map)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            if (
                node.name in TARGET_FUNCTIONS
                or any(x in lowered for x in ("sell", "buy", "persist", "snapshot", "trade"))
            ):
                items.append({
                    "file": str(path.relative_to(ROOT)),
                    "type": "function_definition",
                    "function": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "source": node_source(lines, node),
                })

        if isinstance(node, ast.Call):
            call = ""
            if isinstance(node.func, ast.Name):
                call = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call = node.func.attr

            if call in {
                "qfos_persist_fill_atomic",
                "apply_buy",
                "apply_sell",
                "save_trade",
                "execute",
                "executemany",
            }:
                source = node_source(lines, node)
                lowered = source.lower()
                if call != "execute" or any(x in lowered for x in SQL_MUTATORS):
                    items.append({
                        "file": str(path.relative_to(ROOT)),
                        "type": "call_site",
                        "function": fn,
                        "call": call,
                        "line_start": node.lineno,
                        "line_end": node.end_lineno,
                        "source": source,
                    })

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(x in lowered for x in SQL_MUTATORS):
                items.append({
                    "file": str(path.relative_to(ROOT)),
                    "type": "sql_mutation",
                    "function": fn,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "sql": node.value,
                })

OUT.write_text(json.dumps(items, indent=2), encoding="utf-8")

print(f"ROOT={ROOT}")
print(f"ITEMS={len(items)}")

for x in items:
    if x["type"] == "function_definition":
        print(f"[DEF] {x['file']}::{x['function']} lines={x['line_start']}-{x['line_end']}")
    elif x["type"] == "call_site":
        print(f"[CALL] {x['file']}::{x['function']} call={x['call']} line={x['line_start']}")
    elif x["type"] == "sql_mutation":
        print(f"[SQL] {x['file']}::{x['function']} line={x['line_start']}")
