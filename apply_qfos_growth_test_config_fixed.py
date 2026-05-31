from __future__ import annotations

import json
import re
import shutil
import py_compile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"backup_growth_test_config_fixed_{STAMP}"

print(f"QFOS Fixed Growth Test Patch running in: {ROOT}")
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

def patch_text_value(text: str, key: str, value) -> tuple[str, int]:
    if isinstance(value, int):
        val = str(value)
        num = r"\d+"
    else:
        val = str(value)
        num = r"[-+]?\d+(?:\.\d+)?"

    total = 0

    patterns = [
        # key: int = 2 / key: float = 0.08
        (rf"({re.escape(key)}\s*:\s*(?:int|float)\s*=\s*){num}", rf"\g<1>{val}"),
        # key = 2
        (rf"({re.escape(key)}\s*=\s*){num}", rf"\g<1>{val}"),
        # 'key': 2 or "key": 2
        (rf"(['\"]{re.escape(key)}['\"]\s*:\s*){num}", rf"\g<1>{val}"),
        # object attribute style self.key = 2
        (rf"((?:self|config|risk_rules)\.{re.escape(key)}\s*=\s*){num}", rf"\g<1>{val}"),
    ]

    for pat, repl in patterns:
        text, n = re.subn(pat, repl, text)
        total += n

    return text, total

def patch_json_obj(obj):
    changed = False

    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in TARGETS:
                obj[k] = TARGETS[k]
                changed = True
            else:
                child_changed = patch_json_obj(obj[k])
                changed = changed or child_changed

    elif isinstance(obj, list):
        for item in obj:
            child_changed = patch_json_obj(item)
            changed = changed or child_changed

    return changed

# Patch JSON config files.
for path in [
    ROOT / "winning_strategy_config.json",
    ROOT / "qfos_expectancy_config.json",
    ROOT / "config.json",
    ROOT / "strategy_config.json",
]:
    if not path.exists():
        continue

    backup_file(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue

    if patch_json_obj(data):
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touched.append(str(path.relative_to(ROOT)))

# Patch Python files.
for path in [
    ROOT / "main.py",
    ROOT / "qfos_winning_strategy.py",
    ROOT / "qfos_expectancy_patch.py",
    ROOT / "qfos_expectancy.py",
]:
    if not path.exists():
        continue

    backup_file(path)
    text = path.read_text(encoding="utf-8")
    original = text
    replacements = 0

    for key, value in TARGETS.items():
        text, n = patch_text_value(text, key, value)
        replacements += n

    if text != original:
        path.write_text(text, encoding="utf-8")
        touched.append(f"{path.relative_to(ROOT)} ({replacements} replacements)")

# Compile main.py.
main_py = ROOT / "main.py"
if main_py.exists():
    py_compile.compile(str(main_py), doraise=True)

# Write marker.
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
    "note": "Fixed growth patch. Do not judge run until /status risk_rules show target values.",
}
(ROOT / "qfos_growth_test_marker.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
touched.append("qfos_growth_test_marker.json")

print("")
print("Fixed Growth Test Patch complete.")
print(f"Backup folder: {BACKUP}")
print("")
print("Touched files:")
for item in touched:
    print(f" - {item}")

print("")
print("Applied target values:")
for k, v in TARGETS.items():
    print(f" - {k}: {v}")

print("")
print("Important: after Docker restart, /status risk_rules MUST show the new values.")
