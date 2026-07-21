#!/usr/bin/env python3
"""
TASK 001A -- Canonical Dataset Extraction (read-only)

Run this INSIDE the running Docker environment, e.g.:

    docker compose exec quant python research/export_trades_snapshot.py

or from any host with network access to the postgres service:

    DATABASE_URL=postgresql+psycopg2://qfos:qfos_password@localhost:5432/quant_fund_os \
        python research/export_trades_snapshot.py

It performs ONLY read-only operations:
  - opens the connection in a read-only transaction (SET default_transaction_read_only = on)
  - never issues INSERT/UPDATE/DELETE/DDL
  - writes exactly two output files:
      research/trades_snapshot.csv   (raw `trades` rows, every column, no derived fields)
      research/trades_snapshot_report.json  (all the TASK 001A analytical answers)
  - prints every SQL statement it executes (the required SQL log) to stdout

No code in the running app is modified. This script is standalone.
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

SQL_LOG = []


def log_sql(label, sql):
    SQL_LOG.append({"label": label, "sql": sql.strip()})
    print(f"-- [{label}] --\n{sql.strip()}\n")


def get_database_url():
    # Mirror core/config.py's resolution: env var DATABASE_URL takes precedence.
    url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL / DB_URL not set in this environment. "
            "Run this script inside the quant container (docker compose exec quant ...) "
            "where the real environment is already configured, or export it manually."
        )
    if not url.startswith(("postgresql://", "postgresql+psycopg2://", "postgres://")):
        raise RuntimeError(
            f"DATABASE_URL does not look like PostgreSQL: {url!r}. "
            "This script intentionally refuses to run against SQLite."
        )
    # psycopg2 doesn't understand the 'postgresql+psycopg2://' SQLAlchemy dialect prefix.
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "trades_snapshot.csv")
    report_path = os.path.join(out_dir, "trades_snapshot_report.json")

    dsn = get_database_url()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat()}

    # Enforce read-only at the session level -- any accidental write will error out.
    ro_sql = "SET default_transaction_read_only = on;"
    log_sql("enforce_read_only", ro_sql)
    cur.execute(ro_sql)

    # 1. Database fingerprint
    for key, sql in [
        ("current_database", "SELECT current_database();"),
        ("version", "SELECT version();"),
        ("now", "SELECT NOW();"),
    ]:
        log_sql(f"fingerprint.{key}", sql)
        cur.execute(sql)
        report.setdefault("fingerprint", {})[key] = list(cur.fetchone().values())[0]

    # Confirm table exists & get column list (needed to safely branch on optional cols)
    col_sql = """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'trades'
        ORDER BY ordinal_position;
    """
    log_sql("trades.columns", col_sql)
    cur.execute(col_sql)
    columns = [r["column_name"] for r in cur.fetchall()]
    report["trades_columns"] = columns
    if not columns:
        print("[FATAL] `trades` table not found in public schema of this database.", file=sys.stderr)
        sys.exit(1)

    # 2. Trade inventory
    sql = "SELECT COUNT(*) AS n FROM trades;"
    log_sql("trade_inventory", sql)
    cur.execute(sql)
    report["trade_count"] = cur.fetchone()["n"]

    # 3. BUY / SELL
    sql = "SELECT side, COUNT(*) AS n FROM trades GROUP BY side ORDER BY side;"
    log_sql("buy_sell_breakdown", sql)
    cur.execute(sql)
    report["side_breakdown"] = {r["side"]: r["n"] for r in cur.fetchall()}

    # 4. Completed trades -- side = 'sell' is the definition (see extract_trades_snapshot.sql
    # for the full rationale). Also report is_exit-based count IF that column exists,
    # for cross-checking.
    sql = "SELECT COUNT(*) AS n FROM trades WHERE side = 'sell';"
    log_sql("completed_trades.side_eq_sell", sql)
    cur.execute(sql)
    report["completed_trades_side_eq_sell"] = cur.fetchone()["n"]

    if "is_exit" in columns:
        sql = "SELECT COUNT(*) AS n FROM trades WHERE is_exit = true;"
        log_sql("completed_trades.is_exit_true", sql)
        cur.execute(sql)
        report["completed_trades_is_exit_true"] = cur.fetchone()["n"]
    else:
        report["completed_trades_is_exit_true"] = None
        report["is_exit_column_present"] = False

    # 5. Timestamp coverage
    sql = "SELECT MIN(created_at) AS min_ts, MAX(created_at) AS max_ts FROM trades;"
    log_sql("timestamp_coverage", sql)
    cur.execute(sql)
    row = cur.fetchone()
    report["timestamp_coverage"] = {
        "min_created_at": row["min_ts"].isoformat() if row["min_ts"] else None,
        "max_created_at": row["max_ts"].isoformat() if row["max_ts"] else None,
    }

    # 6. Null completeness
    analytical_cols = [
        "trade_uuid", "strategy", "confidence", "regime",
        "exit_reason", "mfe", "mae", "peak_price", "trough_price", "pnl",
    ]
    null_report = {}
    for col in analytical_cols:
        if col not in columns:
            null_report[col] = {"present": False}
            continue
        sql = f"""
            SELECT COUNT(*) AS total, COUNT({col}) AS non_null
            FROM trades;
        """
        log_sql(f"null_completeness.{col}", sql)
        cur.execute(sql)
        r = cur.fetchone()
        total, non_null = r["total"], r["non_null"]
        null_count = total - non_null
        null_pct = round(100.0 * null_count / total, 2) if total else None
        null_report[col] = {
            "present": True,
            "count": total,
            "null_count": null_count,
            "null_pct": null_pct,
        }
    report["null_completeness"] = null_report

    # 7. Distinct values
    distinct_report = {}
    for col in ["strategy", "regime", "exit_reason"]:
        if col not in columns:
            distinct_report[col] = {"present": False}
            continue
        sql = f"SELECT {col}, COUNT(*) AS n FROM trades GROUP BY {col} ORDER BY n DESC;"
        log_sql(f"distinct_values.{col}", sql)
        cur.execute(sql)
        distinct_report[col] = {
            "present": True,
            "values": [{"value": r[col], "count": r["n"]} for r in cur.fetchall()],
        }
    report["distinct_values"] = distinct_report

    # 8. Export -- raw columns only, no derived fields, ordered by id if present else created_at
    order_col = "id" if "id" in columns else "created_at"
    export_cols = ", ".join(columns)
    export_sql = f"SELECT {export_cols} FROM trades ORDER BY {order_col};"
    log_sql("export_snapshot", export_sql)
    cur.execute(export_sql)
    rows = cur.fetchall()

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    report["export_row_count"] = len(rows)
    report["export_path"] = csv_path

    # Rollback (not commit) -- this session never wrote anything, but be explicit.
    conn.rollback()
    cur.close()
    conn.close()

    report["sql_log"] = SQL_LOG
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n[DONE] Wrote {len(rows)} rows to {csv_path}")
    print(f"[DONE] Wrote full report + SQL log to {report_path}")


if __name__ == "__main__":
    main()
