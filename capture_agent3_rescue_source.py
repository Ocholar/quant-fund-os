from pathlib import Path
import re

targets = [
    Path("main.py"),
    Path("ai/autonomous_agent.py"),
    Path("ai/evolutionary_engine.py"),
    Path("ai/rl_allocator.py"),
]

patterns = [
    r"ALLOCATOR_RESCUE",
    r"evo_allocator_rescue",
    r"RESCUE_DECISION",
    r"symbol_quarantine",
    r"blocked_until",
    r"sideways_stop_loss_exit",
    r"entry_quality_top_n",
    r"entry_min_signal_sideways",
    r"one_tick",
    r"existing_position",
    r"positions",
    r"confidence",
]

def emit_context(lines, idx, radius=20):
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    for n in range(start, end):
        print(f"{n+1:06d}: {lines[n]}")
    print("-" * 110)

for path in targets:
    print("=" * 110)
    print(f"FILE: {path}")
    print("=" * 110)

    if not path.exists():
        print("MISSING")
        continue

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []

    for i, line in enumerate(lines):
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            hits.append(i)

    merged = []
    for idx in hits:
        if not merged or idx - merged[-1] > 45:
            merged.append(idx)

    for idx in merged:
        emit_context(lines, idx)

print("CAPTURE_COMPLETE")
