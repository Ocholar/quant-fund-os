"""
analytics/cli.py — Analytics CLI

Read-only entry point for generating analytics reports from the live database.

Usage (from repo root):
    python -m analytics.cli                       # all reports
    python -m analytics.cli --report edge         # aggregate edge report only
    python -m analytics.cli --report strategy     # strategy ranking only
    python -m analytics.cli --export trades.csv   # export Canonical Trade Dataset
    python -m analytics.cli --live-only           # filter to live=1 trades only
    python -m analytics.cli --regime trending     # filter to a specific regime
    python -m analytics.cli --strategy take_profit  # filter to a specific strategy

Environment:
    DATABASE_URL — PostgreSQL connection string (falls back to quant.db SQLite)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB connection helper (read-only)
# ---------------------------------------------------------------------------

def _get_connection():
    """
    Return a read-only DB connection.

    Tries PostgreSQL first (DATABASE_URL), then falls back to local quant.db.
    """
    db_url = os.getenv("DATABASE_URL", "")

    if db_url:
        try:
            from sqlalchemy import create_engine
            # force read-only session if possible
            engine = create_engine(db_url, pool_pre_ping=True)
            conn = engine.connect()
            log.info("Connected to PostgreSQL at %s", db_url.split("@")[-1] if "@" in db_url else db_url)
            return conn
        except Exception as exc:
            log.warning("PostgreSQL connection failed (%s) — falling back to SQLite", exc)

    # SQLite fallback
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(repo_root, "quant.db")
    if not os.path.exists(db_path):
        log.error("No database found: DATABASE_URL not set and %s missing", db_path)
        sys.exit(1)

    try:
        from sqlalchemy import create_engine
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        conn = engine.connect()
        log.info("Connected to SQLite at %s", db_path)
        return conn
    except Exception as exc:
        log.error("SQLite connection failed: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _apply_filters(
    lifecycles: list[dict],
    live_only: bool = False,
    regime: str | None = None,
    strategy: str | None = None,
    symbol: str | None = None,
) -> list[dict]:
    out = lifecycles
    if live_only:
        out = [lc for lc in out if bool(lc.get("live"))]
    if regime:
        out = [lc for lc in out if str(lc.get("regime") or "").lower() == regime.lower()]
    if strategy:
        out = [lc for lc in out if str(lc.get("strategy") or "").lower() == strategy.lower()]
    if symbol:
        out = [lc for lc in out if str(lc.get("symbol") or "").upper() == symbol.upper()]
    return out


# ---------------------------------------------------------------------------
# Snapshot export
# ---------------------------------------------------------------------------

def _save_experiment_snapshot(dir_path: str, filtered_lifecycles: list[dict], all_lifecycles: list[dict], filter_desc: str):
    import json
    import csv
    from analytics.metrics import edge_report, strategy_report

    os.makedirs(dir_path, exist_ok=True)

    # 1. Edge report
    edge_data = edge_report(filtered_lifecycles)
    with open(os.path.join(dir_path, "edge_report.json"), "w", encoding="utf-8") as f:
        json.dump(edge_data, f, indent=2)

    # 2. Strategy report
    strat_data = strategy_report(filtered_lifecycles)
    with open(os.path.join(dir_path, "strategy_report.json"), "w", encoding="utf-8") as f:
        json.dump(strat_data, f, indent=2)

    # 3. CSV
    if filtered_lifecycles:
        export_keys = [k for k in filtered_lifecycles[0].keys() if not k.startswith("_")]
        with open(os.path.join(dir_path, "trades.csv"), "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=export_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(filtered_lifecycles)

    # 4. Config summary
    config_summary = {
        "snapshot_time": __import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc).isoformat(),
        "total_lifecycles_in_db": len(all_lifecycles),
        "exported_lifecycles": len(filtered_lifecycles),
        "filter_description": filter_desc
    }
    with open(os.path.join(dir_path, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config_summary, f, indent=2)

    print(f"  Saved full experiment snapshot to: {dir_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Quant Fund OS Analytics CLI (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--report",
        choices=["edge", "strategy", "all"],
        default="all",
        help="Which report to generate (default: all)",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        default=None,
        help="Export Canonical Trade Dataset to a CSV file at PATH",
    )
    parser.add_argument(
        "--export-run",
        metavar="DIR",
        default=None,
        help=(
            "Save a full experiment snapshot (edge_report.json, strategy_report.json, "
            "trades.csv) to DIR. Directory is created if it does not exist."
        ),
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        default=False,
        help="Filter to live=1 trades only",
    )
    parser.add_argument(
        "--regime",
        default=None,
        help="Filter by regime label (e.g. trending, ranging)",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Filter by strategy name",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Filter by trading symbol (e.g. BTCUSDT)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Import lazily so the module works even if pandas is absent
    from analytics.dataset import build_canonical_dataset, export_canonical_dataset_csv
    from analytics.metrics import print_edge_report, print_strategy_report

    conn = _get_connection()

    try:
        log.info("Building Canonical Trade Dataset…")
        all_lifecycles = build_canonical_dataset(conn)

        if not all_lifecycles:
            print("No completed trades found in the database.")
            return

        # Apply filters
        lifecycles = _apply_filters(
            all_lifecycles,
            live_only=args.live_only,
            regime=args.regime,
            strategy=args.strategy,
            symbol=args.symbol,
        )

        active_filters = []
        if args.live_only:     active_filters.append("live=1")
        if args.regime:        active_filters.append(f"regime={args.regime}")
        if args.strategy:      active_filters.append(f"strategy={args.strategy}")
        if args.symbol:        active_filters.append(f"symbol={args.symbol}")
        filter_desc = " | ".join(active_filters) or "none"

        print(f"\n  Total completed lifecycles: {len(all_lifecycles)}")
        print(f"  After filters ({filter_desc}): {len(lifecycles)}")

        if not lifecycles:
            print("  No trades match the applied filters.")
            return

        # Reports
        if args.report in ("edge", "all"):
            print_edge_report(lifecycles, title=f"AGGREGATE EDGE REPORT [{filter_desc}]")

        if args.report in ("strategy", "all"):
            print_strategy_report(lifecycles)

        # CSV export
        if args.export:
            from analytics.dataset import export_canonical_dataset_csv
            n = export_canonical_dataset_csv(conn, args.export)
            print(f"  Exported {n} rows to {args.export}")

        # Full experiment snapshot
        if args.export_run:
            _save_experiment_snapshot(args.export_run, lifecycles, all_lifecycles, filter_desc)

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
