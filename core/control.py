import os

KILL_SWITCH_FILE = "/app/runtime/KILL_SWITCH"


def ensure_runtime_dir():
    os.makedirs("/app/runtime", exist_ok=True)


def is_paused():
    return os.path.exists(KILL_SWITCH_FILE)


def pause_bot():
    ensure_runtime_dir()
    with open(KILL_SWITCH_FILE, "w", encoding="utf-8") as f:
        f.write("paused")
    return True


def resume_bot():
    if os.path.exists(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
    return True
