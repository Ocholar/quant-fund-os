from pathlib import Path
import ast
import json

path = Path("main.py")
src = path.read_bytes().decode("utf-8-sig")
lines = src.splitlines()
tree = ast.parse(src)

wanted = {
    "QFOS_OPPORTUNITY_MODE_ENABLED",
    "QFOS_OPP_MAX_TOTAL_EXPOSURE_PCT_SIDEWAYS",
    "QFOS_OPP_ENTRY_ENABLE_BELOW_EXPOSURE_PCT",
    "QFOS_OPP_MEDIUM_CONFIDENCE",
    "QFOS_OPP_HIGH_CONFIDENCE",
    "QFOS_OPP_HIGH_SIGNAL",
    "QFOS_EXIT_SIDEWAYS_STOP_LOSS_PCT",
    "QFOS_EXIT_STOP_LOSS_PCT",
    "QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_AGE",
    "QFOS_EXIT_SIDEWAYS_STAGNATION_MIN_PNL",
    "QFOS_EXIT_SIDEWAYS_STAGNATION_MAX_PNL",
    "QFOS_EXIT_MAX_HOLD_MINUTES",
    "QFOS_SIDEWAYS_MAX_HOLD_MINUTES",
    "FULL_TAKE_PROFIT_PCT",
    "TAKE_PROFIT_PCT",
}

def context(line_no, before=4, after=12):
    start = max(1, line_no - before)
    end = min(len(lines), line_no + after)
    return "\n".join(
        f"{n:05d}: {lines[n - 1]}"
        for n in range(start, end + 1)
    )

hits = []

for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                hits.append({
                    "name": target.id,
                    "line": node.lineno,
                    "context": context(node.lineno),
                })

    if isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id in wanted:
            hits.append({
                "name": node.target.id,
                "line": node.lineno,
                "context": context(node.lineno),
            })

for needle in (
    "_qfos_opp_can_override_entry_reject",
    "opportunity_mode_override",
    "sideways_take_profit_exit",
    "sideways_stagnation_exit",
):
    for number, line in enumerate(lines, start=1):
        if needle.lower() in line.lower():
            hits.append({
                "name": needle,
                "line": number,
                "context": context(number, 6, 20),
            })

print(json.dumps({
    "source": "main.py",
    "matches": hits,
}, indent=2))