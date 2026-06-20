import json
import sys
from pathlib import Path

items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

atomic_calls = [
    x for x in items
    if x.get("type") == "call_site"
    and x.get("call") == "qfos_persist_fill_atomic"
]

direct_trade_writes = [
    x for x in items
    if x.get("type") == "sql_mutation"
    and "trades" in x.get("sql", "").lower()
    and x.get("function") != "qfos_persist_fill_atomic"
]

direct_position_writes = [
    x for x in items
    if x.get("type") == "sql_mutation"
    and "positions" in x.get("sql", "").lower()
    and x.get("function") != "qfos_persist_fill_atomic"
]

direct_snapshot_writes = [
    x for x in items
    if x.get("type") == "sql_mutation"
    and "portfolio_snapshots" in x.get("sql", "").lower()
    and x.get("function") != "qfos_persist_fill_atomic"
]

def show(title, rows):
    print(f"\n=== {title} ===")
    if not rows:
        print("NONE")
        return
    for x in rows:
        print(
            f"{x['file']} :: {x.get('function')} :: "
            f"line {x['line_start']}"
        )
        print((x.get("sql") or x.get("source") or "")[:1500])
        print("---")

show("ATOMIC CALL SITES", atomic_calls)
show("DIRECT TRADE WRITES OUTSIDE ATOMIC", direct_trade_writes)
show("DIRECT POSITION WRITES OUTSIDE ATOMIC", direct_position_writes)
show("DIRECT SNAPSHOT WRITES OUTSIDE ATOMIC", direct_snapshot_writes)

print(f"\nATOMIC_CALLS={len(atomic_calls)}")
print(f"DIRECT_TRADE_WRITES={len(direct_trade_writes)}")
print(f"DIRECT_POSITION_WRITES={len(direct_position_writes)}")
print(f"DIRECT_SNAPSHOT_WRITES={len(direct_snapshot_writes)}")
