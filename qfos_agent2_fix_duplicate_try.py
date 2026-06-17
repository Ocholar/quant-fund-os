from pathlib import Path

path = Path("main.py")
lines = path.read_text(encoding="utf-8").splitlines()

fixed = []
removed = 0
i = 0

while i < len(lines):
    current = lines[i]
    nxt = lines[i + 1] if i + 1 < len(lines) else None

    # Fix exact broken pattern:
    #     try:
    #     try:
    if current.strip() == "try:" and nxt is not None and nxt.strip() == "try:":
        fixed.append(current)
        removed += 1
        i += 2
        continue

    fixed.append(current)
    i += 1

if removed < 1:
    raise SystemExit("DUPLICATE_TRY_FIX_FAILED: no consecutive try/try block found")

path.write_text("\n".join(fixed) + "\n", encoding="utf-8")

print("DUPLICATE_TRY_FIX_OK")
print("REMOVED_DUPLICATE_TRY_COUNT", removed)
