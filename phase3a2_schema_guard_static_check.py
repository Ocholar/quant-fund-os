from pathlib import Path

s = Path("main.py").read_text(encoding="utf-8-sig")

required = [
    "BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1",
    "def qfos_ensure_execution_accounting_schema_and_guards(",
    "ALTER TABLE trades ADD COLUMN is_exit",
    "ALTER TABLE trades ADD COLUMN exit_reason",
    "CREATE TRIGGER qfos_block_stale_position_insert",
    "CREATE TRIGGER qfos_block_stale_position_update",
]

for item in required:
    if item not in s:
        print("FAIL missing:", item)
        raise SystemExit(1)

if s.count("BEGIN QFOS_EXECUTION_ACCOUNTING_SCHEMA_GUARD_V1") != 1:
    print("FAIL duplicate schema guard blocks")
    raise SystemExit(1)

print("PASS: Phase 3A2 schema guard exists exactly once")
