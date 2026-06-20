import ast
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()

wanted = {
    "qfos_persist_fill_atomic",
    "qfos_exit_lifecycle_execute_sell",
    "qfos_exit_lifecycle_evaluate_once",
    "_qfos_poswd_close_position",
    "_qfos_pe_sell",
    "_qfos_watchdog_close_worst_loser_once",
    "apply_buy",
    "apply_sell",
    "save_trade",
    "generate_sells",
}

with out.open("w", encoding="utf-8") as f:
    for path in root.rglob("*.py"):
        if "agent5_" in str(path) or path.name.startswith("inventory_"):
            continue
        try:
            src = path.read_text(encoding="utf-8-sig")
            lines = src.splitlines()
            tree = ast.parse(src)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
                f.write(f"\n\n===== {path.relative_to(root)} :: {node.name} :: {node.lineno}-{node.end_lineno} =====\n")
                f.write("\n".join(lines[node.lineno - 1:node.end_lineno]))
                f.write("\n")
