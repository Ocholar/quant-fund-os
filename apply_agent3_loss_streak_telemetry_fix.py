from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8", errors="replace")

old = '''        elif quarantined:
            if "stop_loss" in str(db_state["quarantine_reason"]):
                reject_reason = "recent_stop_loss"
            else:
                reject_reason = "quarantined"
'''

new = '''        elif quarantined:
            _qfos_quarantine_reason = str(db_state["quarantine_reason"] or "")
            if "loss_streak" in _qfos_quarantine_reason:
                reject_reason = "loss_streak"
            elif "stop_loss" in _qfos_quarantine_reason:
                reject_reason = "recent_stop_loss"
            else:
                reject_reason = "quarantined"
'''

if old not in src:
    raise SystemExit("PATCH_FAILED: rescue quarantine decision block not found")

src = src.replace(old, new, 1)
path.write_text(src, encoding="utf-8")

print("PATCH_WRITE_OK")
