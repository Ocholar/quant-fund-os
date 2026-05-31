from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"backup_growth_test_config_{STAMP}"

print(f"QFOS Growth Test Patch running in: {ROOT}")
BACKUP.mkdir(exist_ok=True)

TARGETS = {
    "sideways_max_entries_per_hour": 4,
    "max_total_exposure_pct": 0.12,
    "max_symbol_exposure_pct": 0.04,
    "entry_min_signal_sideways": 0.0012,
}

touched = []

def backup_file(path: Path) -> None:
    if path.exists():
        dst = BACKUP / path.name
        shutil.copy2(path, dst)

def replace_regex(text: str, key: str, value) -> tuple[str, bool]:
    """
    Handles patterns like:
      sideways_max_entries_per_hour: int = 2
      self.sideways_max_entries_per_hour = 2
      "sideways_max_entries_per_hour": 2
      'sideways_max_entries_per_hour': 2
    """
    if isinstance(value, int):
        val = str(value)
        num_pattern = r"\d+"
    else:
        val = str(value)
        num_pattern = r"[-+]?\d+(?:\.\d+)?"

    patterns = [
        (
            rf"({re.escape(key)}\s*:\s*(?:int|float)\s*=\s*){num_pattern}",
            rf"\g<1>{val}",
        ),
        (
            rf"({re.escape(key)}\s*=\s*){num_pattern}",
            rf"\g<1>{val}",
        ),
        (
            rf"(['\"]{re.escape(key)}['\"]\s*:\s*){num_pattern}",
            rf"\g<1>{val}",
        ),
    ]

    changed_any = False
    for pat, repl in patterns:
        new_text, n = re.subn(pat, repl, text)
        if n:
            text = new_text
            changed_any = True

    return text, changed_any

# 1) Patch JSON config files first.
json_candidates = [
    ROOT / "winning_strategy_config.json",
    ROOT / "qfos_expectancy_config.json",
    ROOT / "config.json",
    ROOT / "strategy_config.json",
]

for path in json_candidates:
    if not path.exists():
        continue

    backup_file(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue

    changed = False

    def walk(obj):
        nonlocal changed
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in TARGETS:
                    obj[k] = TARGETS[k]
                    changed = True
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)

    if changed:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touched.append(str(path.relative_to(ROOT)))

# 2) Patch Python files where risk defaults are hardcoded.
python_candidates = [
    ROOT / "main.py",
    ROOT / "qfos_winning_strategy.py",
    ROOT / "qfos_expectancy_patch.py",
    ROOT / "qfos_expectancy.py",
]

for path in python_candidates:
    if not path.exists():
        continue

    backup_file(path)
    text = path.read_text(encoding="utf-8")
    original = text

    for key, value in TARGETS.items():
        text, _ = replace_regex(text, key, value)

    if text != original:
        path.write_text(text, encoding="utf-8")
        touched.append(str(path.relative_to(ROOT)))

# 3) Write an explicit growth test marker.
marker = {
    "mode": "growth_test_mode",
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "changes": TARGETS,
    "baseline_previous_24h": {
        "starting_equity": 100.00,
        "ending_equity": 100.31,
        "total_pnl": 0.31,
        "total_trades": 83,
        "win_rate": 0.5769,
        "risk_off_exit_count": 0,
        "emergency_exit_count": 0,
    },
    "test_instruction": "Continue another 24h paper run without resetting. Compare post-patch growth behavior against the prior 24h baseline.",
}
(ROOT / "qfos_growth_test_marker.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
touched.append("qfos_growth_test_marker.json")

print("")
print("Growth Test Patch complete.")
print(f"Backup folder: {BACKUP}")
print("")
print("Touched files:")
for f in touched:
    print(f" - {f}")

print("")
print("Applied target values:")
for k, v in TARGETS.items():
    print(f" - {k}: {v}")

print("")
print("Next: rebuild/restart Docker and verify /status risk_rules.")
