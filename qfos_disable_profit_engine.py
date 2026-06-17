from pathlib import Path
import re

p = Path("main.py")
text = p.read_text(encoding="utf-8")

# Hard-disable DB-level profit engine.
text = re.sub(
    r"QFOS_PROFIT_ENGINE_ENABLED\s*=\s*globals\(\)\.get\(\s*[\"']QFOS_PROFIT_ENGINE_ENABLED[\"']\s*,\s*True\s*\)",
    "QFOS_PROFIT_ENGINE_ENABLED = False  # QFOS_DISABLE_DB_PROFIT_ENGINE_V1: disabled duplicate DB-level sell engine",
    text,
)

# Also catch direct assignment if present.
text = re.sub(
    r"QFOS_PROFIT_ENGINE_ENABLED\s*=\s*True",
    "QFOS_PROFIT_ENGINE_ENABLED = False  # QFOS_DISABLE_DB_PROFIT_ENGINE_V1",
    text,
)

# Prevent the thread from starting even if a later block overrides the flag.
text = text.replace(
    "_qfos_start_profit_engine()",
    "print('[PROFIT_ENGINE] disabled_for_24h_stability_run', flush=True)  # _qfos_start_profit_engine() disabled"
)

# Keep fallback scout disabled.
text = re.sub(
    r"QFOS_SCOUT_FALLBACK_ENABLED\s*=\s*True",
    "QFOS_SCOUT_FALLBACK_ENABLED = False  # fallback scout disabled",
    text,
)

# Add visible marker.
if "QFOS_DISABLE_DB_PROFIT_ENGINE_V1" not in text:
    text += "\n# QFOS_DISABLE_DB_PROFIT_ENGINE_V1\n"

p.write_text(text, encoding="utf-8")
print("Disabled DB-level Profit Engine and fallback scout.")
