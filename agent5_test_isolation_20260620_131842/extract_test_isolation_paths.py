from pathlib import Path
import ast
import re

p = Path("main.py")
src = p.read_text(encoding="utf-8-sig")
lines = src.splitlines()

patterns = [
    r"PAUSE_HARD_BLOCK",
    r"pause_state_known",
    r"is_paused",
    r"get_control_state",
    r"QFOS_LOOP_START",
    r"daemon_started",
    r"threading\.Thread",
    r"start_cash_equity_authority_daemon",
    r"start.*watchdog",
    r"start.*reconciler",
    r"if __name__",
]

for pattern in patterns:
    print(f"\n===== PATTERN: {pattern} =====")
    found = False
    for m in re.finditer(pattern, src, flags=re.I):
        found = True
        line_no = src.count("\n", 0, m.start()) + 1
        start = max(1, line_no - 18)
        end = min(len(lines), line_no + 30)
        print(f"\n--- lines {start}-{end} ---")
        for n in range(start, end + 1):
            print(f"{n:>6}: {lines[n-1]}")
    if not found:
        print("NOT_FOUND")

print("\n===== TOP-LEVEL CALLS AFTER AST PARSE =====")
tree = ast.parse(src)

for node in tree.body:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        print(f"line={node.lineno} call={ast.unparse(node.value.func)}")
    elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
        print(f"line={node.lineno} assign_call={ast.unparse(node.value.func)}")
