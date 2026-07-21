import json
import time
from datetime import datetime, timezone
import subprocess
from pathlib import Path
from sqlalchemy import create_engine, text

def snapshot():
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": "",
        "db_checksum": "",
        "equity": 0.0,
        "cash": 0.0,
        "open_positions": [],
        "allocator_stats": {},
        "config": {}
    }
    
    # Git
    try:
        data["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
    except Exception:
        pass
        
    # SQLite 
    db_path = Path("data/quant.db")
    if not db_path.exists():
        db_path = Path("../data/quant.db")
        if not db_path.exists():
            db_path = None
    if db_path:
        try:
            # Simple file size proxy for checksum
            data["db_checksum"] = str(db_path.stat().st_size)
        except Exception:
            pass

    # Config
    try:
        from core.config import settings
        data["config"] = {
            "pm_v2_enabled": settings.pm_v2_enabled,
            "pm_v2_dry_run": settings.pm_v2_dry_run,
            "sideways_max_entries_per_hour": settings.sideways_max_entries_per_hour,
        }
    except Exception:
        pass
        
    # Open positions
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine("sqlite:///data/quant.db")
        with engine.begin() as conn:
            pos_rows = conn.execute(text("SELECT * FROM positions WHERE quantity > 0")).mappings().fetchall()
            data["open_positions"] = [dict(r) for r in pos_rows]
            
            # Balances (if stored in a table)
            # Assuming equity/cash is tracked elsewhere, but this is a placeholder
    except Exception:
        pass

    out_file = Path("research/baseline_snapshot.json")
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Baseline snapshot saved to {out_file}")

if __name__ == "__main__":
    snapshot()
