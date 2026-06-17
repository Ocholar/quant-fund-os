from pathlib import Path

lines = Path("main.py").read_text(encoding="utf-8").splitlines()

bad = []
for i in range(len(lines) - 1):
    if lines[i].strip() == "try:" and lines[i + 1].strip() == "try:":
        bad.append((i + 1, lines[i], lines[i + 1]))

if bad:
    print("FAILED_DUPLICATE_TRY_FOUND")
    for row in bad:
        print(row)
    raise SystemExit(1)

print("PASS_NO_DUPLICATE_TRY")
