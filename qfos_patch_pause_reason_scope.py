from pathlib import Path
import re

path = Path("main.py")
lines = path.read_text(encoding="utf-8").splitlines()

def_re = re.compile(r"^(\s*)(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*:")

patches = []
i = 0

while i < len(lines):
    m = def_re.match(lines[i])

    if not m:
        i += 1
        continue

    def_indent = len(m.group(1))
    func_name = m.group(3)
    params = m.group(4)

    body_start = i + 1
    j = body_start

    while j < len(lines):
        line = lines[j]

        if line.strip() == "":
            j += 1
            continue

        line_indent = len(line) - len(line.lstrip())

        if line_indent <= def_indent:
            break

        j += 1

    body_end = j
    body = lines[body_start:body_end]

    body_text = "\n".join(body)

    # Do not add global if pause_reason is a parameter.
    params_clean = params.replace(" ", "")
    has_pause_param = (
        params_clean == "pause_reason"
        or params_clean.startswith("pause_reason,")
        or ",pause_reason," in params_clean
        or params_clean.endswith(",pause_reason")
        or "pause_reason=" in params_clean
    )

    if "pause_reason" in body_text and not has_pause_param:
        already_global = False
        for b in body[:20]:
            if b.strip().startswith("global ") and "pause_reason" in b:
                already_global = True
                break

        if not already_global:
            insert_at = body_start

            # Skip blank lines immediately after def.
            while insert_at < body_end and lines[insert_at].strip() == "":
                insert_at += 1

            # If function starts with a docstring, insert after the docstring block.
            if insert_at < body_end:
                s = lines[insert_at].lstrip()
                if s.startswith('"""') or s.startswith("'''"):
                    quote = '"""' if s.startswith('"""') else "'''"

                    # Single-line docstring.
                    if s.count(quote) >= 2 and len(s) > 3:
                        insert_at += 1
                    else:
                        insert_at += 1
                        while insert_at < body_end and quote not in lines[insert_at]:
                            insert_at += 1
                        if insert_at < body_end:
                            insert_at += 1

            patches.append((insert_at, " " * (def_indent + 4) + "global pause_reason", func_name))

    i = body_end if body_end > i else i + 1

# Apply insertions bottom-up so indexes remain valid.
for insert_at, global_line, func_name in sorted(patches, key=lambda x: x[0], reverse=True):
    lines.insert(insert_at, global_line)

path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("PAUSE_REASON_SCOPE_PATCH_OK")
print("FUNCTIONS_PATCHED_COUNT", len(patches))
for _, _, func_name in patches:
    print("PATCHED_FUNCTION", func_name)
