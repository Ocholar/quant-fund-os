import ast
import json
from pathlib import Path

ROOT = Path(".")
OUT = Path("execution_inventory.json")

SKIP = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "agent5_canonical_lifecycle_inventory"
}

KEYWORDS = (
    "insert into trades",
    "update trades",
    "delete from trades",
    "insert into positions",
    "update positions",
    "delete from positions",
    "insert into portfolio_snapshots",
    "update portfolio_snapshots",
    "cash",
    "equity",
    "realized_pnl",
    "unrealized_pnl",
    "qfos_persist_fill_atomic",
    "apply_buy",
    "apply_sell",
)

def source_segment(src, node):
    lines = src.splitlines()
    start = max(0, getattr(node, "lineno", 1) - 1)
    end = min(len(lines), getattr(node, "end_lineno", start + 1))
    return "\n".join(lines[start:end])

def function_name_stack(node, parents):
    names = []
    for p in parents:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(p.name)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.append(node.name)
    return ".".join(names) or "<module>"

results = []

for path in ROOT.rglob("*.py"):
    if any(part in SKIP or part.startswith("agent5_canonical_lifecycle_inventory") for part in path.parts):
        continue

    try:
        src = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
    except Exception as exc:
        results.append({
            "file": str(path),
            "type": "parse_error",
            "error": repr(exc),
        })
        continue

    parents = {}

    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def ancestry(node):
        chain = []
        cur = parents.get(id(node))
        while cur is not None:
            chain.append(cur)
            cur = parents.get(id(cur))
        return list(reversed(chain))

    # Function definitions.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.name in {
                    "qfos_persist_fill_atomic",
                    "qfos_exit_lifecycle_execute_sell",
                    "apply_buy",
                    "apply_sell",
                    "save_trade",
                    "qfos_exec_bridge_process_orders",
                    "qfos_exec_bridge_validate_fill",
                    "_qfos_poswd_close_position",
                    "_qfos_pe_sell",
                    "_qfos_watchdog_close_worst_loser_once",
                    "write_portfolio_snapshot",
                    "save_portfolio_snapshot",
                }
                or "sell" in node.name.lower()
                or "buy" in node.name.lower()
                or "persist" in node.name.lower()
                or "snapshot" in node.name.lower()
            ):
                results.append({
                    "file": str(path),
                    "type": "function_definition",
                    "function": function_name_stack(node, ancestry(node)),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "source": source_segment(src, node),
                })

    # Calls and SQL-bearing strings.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = ""
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr

            if call_name in {
                "qfos_persist_fill_atomic",
                "apply_buy",
                "apply_sell",
                "save_trade",
                "execute",
                "executemany",
            }:
                text = source_segment(src, node)
                lowered = text.lower()

                relevant = (
                    call_name != "execute"
                    or any(k in lowered for k in KEYWORDS)
                )

                if relevant:
                    results.append({
                        "file": str(path),
                        "type": "call_site",
                        "function": function_name_stack(node, ancestry(node)),
                        "call": call_name,
                        "line_start": node.lineno,
                        "line_end": node.end_lineno,
                        "source": text,
                    })

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(k in lowered for k in (
                "insert into trades",
                "update positions",
                "insert into positions",
                "insert into portfolio_snapshots",
                "update portfolio_snapshots",
                "delete from positions",
            )):
                results.append({
                    "file": str(path),
                    "type": "sql_mutation",
                    "function": function_name_stack(node, ancestry(node)),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "sql": node.value,
                })

OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

print(f"WROTE {OUT}")
print(f"TOTAL_ITEMS={len(results)}")

for item in results:
    if item["type"] == "sql_mutation":
        print(
            f"[SQL_MUTATION] file={item['file']} "
            f"function={item['function']} line={item['line_start']}"
        )
    elif item["type"] == "call_site":
        print(
            f"[CALL] file={item['file']} "
            f"function={item['function']} call={item['call']} "
            f"line={item['line_start']}"
        )
    elif item["type"] == "function_definition":
        print(
            f"[DEF] file={item['file']} "
            f"function={item['function']} "
            f"line={item['line_start']}-{item['line_end']}"
        )
