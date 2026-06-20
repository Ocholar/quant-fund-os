from pathlib import Path
import ast
import json

src = Path("main.py").read_bytes().decode("utf-8-sig")
lines = src.splitlines()
tree = ast.parse(src)

terms = (
    "ENTRY QUALITY TOP 10",
    "[QUALITY_RANK]",
    "entry_quality_not_top_10",
    "no_candidate_passed",
    "quality_or_risk_gates",
)

def snippet(start, end):
    start = max(1, start)
    end = min(len(lines), end)
    return "\n".join(
        f"{i:05d}: {lines[i - 1]}"
        for i in range(start, end + 1)
    )

results = []

for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue

    end = getattr(node, "end_lineno", node.lineno)
    body = "\n".join(lines[node.lineno - 1:end])

    if any(term.lower() in body.lower() for term in terms):
        results.append({
            "function": node.name,
            "start_line": node.lineno,
            "end_line": end,
            "body": snippet(node.lineno - 5, min(end, node.lineno + 220)),
        })

print(json.dumps(results, indent=2))