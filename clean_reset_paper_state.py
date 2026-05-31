import sqlite3
from pathlib import Path
from datetime import datetime

dbs = list(Path(".").rglob("*.db")) + list(Path(".").rglob("*.sqlite")) + list(Path(".").rglob("*.sqlite3"))

if not dbs:
    raise SystemExit("No database file found.")

# Prefer the first app db found
db = dbs[0]
print("Using DB:", db)

conn = sqlite3.connect(db)
cur = conn.cursor()

tables = {
    r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
}

print("Tables found:", sorted(tables))

# Delete runtime/trading records.
clear_tables = [
    "trades",
    "positions",
    "portfolio",
    "portfolio_snapshots",
    "symbol_quarantine",
    "strategy_scores",
    "orders",
    "fills",
    "risk_events",
    "performance",
    "metrics",
]

for table in clear_tables:
    if table in tables:
        print("Clearing:", table)
        cur.execute(f"DELETE FROM {table}")

# Recreate a clean portfolio row if the table exists.
if "portfolio" in tables:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(portfolio)").fetchall()]
    print("portfolio columns:", cols)

    values = {}
    if "equity" in cols:
        values["equity"] = 100.0
    if "cash" in cols:
        values["cash"] = 100.0
    if "exposure" in cols:
        values["exposure"] = 0.0
    if "drawdown" in cols:
        values["drawdown"] = 0.0
    if "regime" in cols:
        values["regime"] = "SIDEWAYS"
    if "created_at" in cols:
        values["created_at"] = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    if "updated_at" in cols:
        values["updated_at"] = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    if values:
        col_sql = ", ".join(values.keys())
        q_sql = ", ".join(["?"] * len(values))
        cur.execute(
            f"INSERT INTO portfolio ({col_sql}) VALUES ({q_sql})",
            list(values.values())
        )
        print("Inserted clean portfolio row:", values)

conn.commit()
conn.close()

print("Clean paper reset complete.")
