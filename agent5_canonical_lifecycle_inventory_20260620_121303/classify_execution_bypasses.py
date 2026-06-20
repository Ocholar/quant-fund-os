import json
from pathlib import Path

items = json.loads(Path("execution_inventory.json").read_text(encoding="utf-8"))

atomic_calls = []
direct_mutations = []

for item in items:
    if item.get("type") == "call_site" and item.get("call") == "qfos_persist_fill_atomic":
        atomic_calls.append(item)

    if item.get("type") == "sql_mutation":
        fn = item.get("function", "")
        if "qfos_persist_fill_atomic" not in fn:
            direct_mutations.append(item)

print("=== ATOMIC CALL SITES ===")
for item in atomic_calls:
    print(f"{item['file']} :: {item['function']} :: line {item['line_start']}")

print("")
print("=== DIRECT SQL MUTATIONS OUTSIDE ATOMIC FUNCTION ===")
if not direct_mutations:
    print("NONE_FOUND")
else:
    for item in direct_mutations:
        print(f"{item['file']} :: {item['function']} :: line {item['line_start']}")
        print(item["sql"][:1200].replace("\n", " "))
        print("---")

print("")
print(f"ATOMIC_CALL_SITE_COUNT={len(atomic_calls)}")
print(f"DIRECT_MUTATION_COUNT={len(direct_mutations)}")
