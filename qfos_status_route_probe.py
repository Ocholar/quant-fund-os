from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import urllib.request

ROOT = Path("/app")
out = {}

def read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"__READ_ERROR__:{exc!r}"

def sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception as exc:
        return f"__HASH_ERROR__:{exc!r}"

out["proc_1_cmdline"] = read("/proc/1/cmdline").replace("\x00", " ")
out["start_sh"] = read(ROOT / "start.sh")
out["main_sha256"] = sha256(ROOT / "main.py")
out["api_sha256"] = sha256(ROOT / "services" / "api.py")

try:
    ps = subprocess.run(
        ["sh", "-lc", "ps -ef || ps aux || true"],
        capture_output=True,
        text=True,
        check=False,
    )
    out["processes"] = ps.stdout
except Exception as exc:
    out["processes"] = repr(exc)

files = []
for path in ROOT.rglob("*.py"):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue

    findings = {
        "file": str(path),
        "fastapi_app_lines": [],
        "status_route_lines": [],
        "truth_marker_lines": [],
        "middleware_lines": [],
    }

    for number, line in enumerate(text.splitlines(), start=1):
        if re.search(r"^\s*app\s*=\s*FastAPI\s*\(", line):
            findings["fastapi_app_lines"].append(number)

        if "@app.get" in line and "/status" in line:
            findings["status_route_lines"].append(number)

        if "QFOS_STATUS_TRUTH_CONTRACT_FINAL_V1" in line:
            findings["truth_marker_lines"].append(number)

        if '@app.middleware("http")' in line or "@app.middleware('http')" in line:
            findings["middleware_lines"].append(number)

    if any(findings[key] for key in findings if key != "file"):
        files.append(findings)

out["route_candidates"] = files

try:
    raw = urllib.request.urlopen("http://127.0.0.1:8080/status", timeout=8).read().decode("utf-8")
    payload = json.loads(raw)
    out["status_raw"] = payload
    out["performance_keys"] = sorted((payload.get("performance") or {}).keys())
except Exception as exc:
    out["status_fetch_error"] = repr(exc)

print(json.dumps(out, indent=2, default=str))
