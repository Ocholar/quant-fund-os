from pathlib import Path
import ast
import json
import re

path = Path("main.py")
raw = path.read_bytes()
src = raw.decode("utf-8-sig")
lines = src.splitlines()

tree = ast.parse(src)

def context(line_number, before=8, after=18):
    start = max(1, line_number - before)
    end = min(len(lines), line_number + after)
    return {
        "start": start,
        "end": end,
        "text": "\n".join(
            f"{n:05d}: {lines[n - 1]}"
            for n in range(start, end + 1)
        ),
    }

matches = {
    "allocator_rescue_hook": [],
    "qfos_persist_fill_atomic_definitions": [],
    "qfos_persist_fill_atomic_calls": [],
    "direct_trade_insert_strings": [],
    "trade_persistence_terms": [],
    "rescue_block_markers": [],
}

for number, line in enumerate(lines, start=1):
    lower = line.lower()

    if "allocator_rescue_hook" in lower:
        matches["allocator_rescue_hook"].append(context(number))

    if "qfos_disable_rescue_buys_v3" in lower or "rescue_buy_block" in lower:
        matches["rescue_block_markers"].append(context(number, 3, 8))

    if (
        "insert into trades" in lower
        or "insert_into_trades" in lower
        or "persist_fill" in lower
        or "save_trade" in lower
        or "record_trade" in lower
    ):
        matches["trade_persistence_terms"].append(context(number, 4, 12))

    if "insert into trades" in lower:
        matches["direct_trade_insert_strings"].append(context(number, 6, 18))

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name == "qfos_persist_fill_atomic":
            matches["qfos_persist_fill_atomic_definitions"].append({
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "args": [arg.arg for arg in node.args.args],
                "context": context(node.lineno, 3, 40),
            })

    if isinstance(node, ast.Call):
        fn = node.func
        name = None

        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr

        if name == "qfos_persist_fill_atomic":
            matches["qfos_persist_fill_atomic_calls"].append({
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "context": context(node.lineno, 8, 24),
            })

print(json.dumps({
    "bom_present": raw.startswith(b"\xef\xbb\xbf"),
    "line_count": len(lines),
    "matches": matches,
}, indent=2))