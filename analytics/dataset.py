"""
analytics/dataset.py — Canonical Trade Dataset

Builds a flat-to-flat lifecycle view from the raw `trades` table.

Design decisions (per PM approval):
- Flat-to-flat: a "trade" = all BUYs that open a position + the SELL that closes it.
- Entry VWAP = sum(qty_i * price_i) / sum(qty_i) across all entry fills.
- Exit price = fill_price on the SELL row.
- Hold time = created_at of SELL − created_at of first BUY.
- MFE / MAE = NULL for historical trades (no lifecycle tracking prior to schema upgrade).
  For new trades, values are populated by mark_positions_to_market + exit injection.
- Regime, experiment_id, software_version, configuration_hash come from the BUY rows.
  If multiple BUY rows exist for one lifecycle they should all share the same values
  (any mismatch is logged as a warning, first value wins).

Usage:
    from analytics.dataset import build_canonical_dataset
    rows = build_canonical_dataset(conn)  # returns list[dict]
    # or
    import pandas as pd
    df = pd.DataFrame(build_canonical_dataset(conn))
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _try_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _try_dt(v):
    """Parse a timestamp string → aware datetime, or None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    try:
        s = str(v).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    except Exception:
        pass
    return None


def _hold_seconds(start_dt, end_dt):
    if start_dt is None or end_dt is None:
        return None
    try:
        return (end_dt - start_dt).total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core query
# ---------------------------------------------------------------------------

_FETCH_TRADES_SQL = """
SELECT
    id,
    symbol,
    side,
    COALESCE(quantity, 0)                  AS quantity,
    COALESCE(fill_price, 0)                AS fill_price,
    COALESCE(expected_price, fill_price, 0) AS expected_price,
    COALESCE(pnl, 0)                       AS pnl,
    strategy,
    COALESCE(confidence, 0)                AS confidence,
    COALESCE(is_exit, 0)                   AS is_exit,
    exit_reason,
    trade_uuid,
    regime,
    experiment_id,
    software_version,
    configuration_hash,
    mfe,
    mae,
    peak_price,
    trough_price,
    COALESCE(slippage_bps, 0)              AS slippage_bps,
    COALESCE(live, 0)                      AS live,
    COALESCE(shadow_mode, 0)               AS shadow_mode,
    source,
    created_at
FROM trades
ORDER BY id ASC
"""


def _fetch_raw_trades(conn) -> list[dict]:
    """Execute the trades query, return list of plain dicts."""
    try:
        # SQLAlchemy connection
        from sqlalchemy import text
        rows = conn.execute(text(_FETCH_TRADES_SQL)).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("SQLAlchemy fetch failed (%s), trying raw cursor", exc)

    try:
        # Raw sqlite3 / psycopg2 cursor
        cur = conn.cursor()
        cur.execute(_FETCH_TRADES_SQL)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc2:
        log.error("Raw cursor fetch also failed: %s", exc2)
        return []


# ---------------------------------------------------------------------------
# Flat-to-flat lifecycle matching
# ---------------------------------------------------------------------------

def _group_by_symbol(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "")
        groups.setdefault(sym, []).append(r)
    return groups


def _match_lifecycles(symbol_rows: list[dict]) -> list[dict]:
    """
    Convert a chronologically sorted list of fill rows for one symbol into
    a list of completed flat-to-flat lifecycle records.

    Rules:
    - Accumulate BUY fills until a SELL with is_exit=1 (or side=sell) appears.
    - The SELL closes the lifecycle.
    - If a SELL arrives while no BUY is open, it is logged and skipped.
    - Partial fills (multiple buys before a full exit) are rolled up by VWAP.
    """
    lifecycles = []
    open_buys: list[dict] = []   # accumulator for the current open position

    def _flush(sell_row: dict) -> dict | None:
        """Close the current open_buys accumulator with sell_row."""
        if not open_buys:
            return None

        total_qty = sum(_try_float(r["quantity"], 0.0) for r in open_buys)
        if total_qty <= 0:
            return None

        vwap_entry = (
            sum(_try_float(r["quantity"], 0.0) * _try_float(r["fill_price"], 0.0)
                for r in open_buys)
            / total_qty
        )

        first_buy = open_buys[0]
        first_buy_dt = _try_dt(first_buy.get("created_at"))
        sell_dt = _try_dt(sell_row.get("created_at"))
        hold_secs = _hold_seconds(first_buy_dt, sell_dt)

        exit_price = _try_float(sell_row.get("fill_price"), 0.0)
        exit_pnl   = _try_float(sell_row.get("pnl"), 0.0)

        # Prefer explicit MFE/MAE from the sell row; fall back to None
        mfe          = _try_float(sell_row.get("mfe"))
        mae          = _try_float(sell_row.get("mae"))
        peak_price   = _try_float(sell_row.get("peak_price"))
        trough_price = _try_float(sell_row.get("trough_price"))

        # Metadata from the first BUY (strategy may change on re-entry — first wins)
        strategy     = first_buy.get("strategy") or sell_row.get("strategy")
        regime       = first_buy.get("regime")   or sell_row.get("regime")
        trade_uuid   = (
            sell_row.get("trade_uuid")
            or first_buy.get("trade_uuid")
        )
        experiment_id        = first_buy.get("experiment_id")
        software_version     = first_buy.get("software_version")
        configuration_hash   = first_buy.get("configuration_hash")
        live         = bool(first_buy.get("live") or sell_row.get("live"))
        shadow_mode  = bool(first_buy.get("shadow_mode") or sell_row.get("shadow_mode"))

        # Warn on metadata mismatches across BUY fills
        for key in ("regime", "experiment_id", "software_version", "configuration_hash"):
            vals = {r.get(key) for r in open_buys if r.get(key) is not None}
            if len(vals) > 1:
                log.warning(
                    "Lifecycle %s symbol=%s: multiple %s values across BUY fills: %s",
                    trade_uuid, symbol, key, vals
                )

        # Avg entry slippage across BUY fills (weighted by qty)
        total_slippage_bps = (
            sum(_try_float(r["quantity"], 0.0) * _try_float(r["slippage_bps"], 0.0)
                for r in open_buys)
            / total_qty
        )

        # MFE/MAE quality label
        if mfe is None and mae is None:
            mfe_mae_quality = "unavailable"
        else:
            mfe_mae_quality = "live"

        lc: dict[str, Any] = {
            # Identity
            "symbol": symbol,
            "trade_uuid": trade_uuid,
            "strategy": strategy,
            "regime": regime,
            "experiment_id": experiment_id,
            "software_version": software_version,
            "configuration_hash": configuration_hash,
            "live": live,
            "shadow_mode": shadow_mode,
            # Entry
            "entry_qty": total_qty,
            "entry_vwap": round(vwap_entry, 8),
            "entry_fill_count": len(open_buys),
            "entry_avg_slippage_bps": round(total_slippage_bps, 4),
            "entry_time": first_buy_dt.isoformat() if first_buy_dt else None,
            # Exit
            "exit_price": exit_price,
            "exit_reason": sell_row.get("exit_reason"),
            "exit_slippage_bps": _try_float(sell_row.get("slippage_bps"), 0.0),
            "exit_time": sell_dt.isoformat() if sell_dt else None,
            # P&L
            "realized_pnl": exit_pnl,
            "hold_seconds": hold_secs,
            # Return % relative to entry VWAP
            "return_pct": (
                round((exit_price - vwap_entry) / vwap_entry * 100, 6)
                if vwap_entry > 0 else None
            ),
            # MFE / MAE lifecycle metrics
            "mfe": mfe,
            "mae": mae,
            "peak_price": peak_price,
            "trough_price": trough_price,
            "mfe_mae_quality": mfe_mae_quality,
            # Internals (for debugging)
            "_buy_ids": [r["id"] for r in open_buys],
            "_sell_id": sell_row.get("id"),
        }
        return lc

    symbol = symbol_rows[0].get("symbol") if symbol_rows else "?"

    for row in symbol_rows:
        side = str(row.get("side") or "").lower()
        is_exit = bool(row.get("is_exit"))

        if side == "buy" and not is_exit:
            open_buys.append(row)

        elif side == "sell" or is_exit:
            lc = _flush(row)
            if lc is not None:
                lifecycles.append(lc)
            else:
                log.debug(
                    "SELL row id=%s symbol=%s arrived with no open BUYs — skipped",
                    row.get("id"), symbol
                )
            open_buys = []   # reset for next position

    # If open_buys remain at end of history, position is still open — skip
    if open_buys:
        log.debug(
            "symbol=%s: %d open BUY fill(s) remain unclosed — position still active",
            symbol, len(open_buys)
        )

    return lifecycles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_canonical_dataset(conn) -> list[dict]:
    """
    Return the Canonical Trade Dataset as a list of lifecycle dicts.

    Each dict represents one complete flat-to-flat trade.
    Open positions are excluded.

    Args:
        conn: SQLAlchemy connection or raw sqlite3/psycopg2 connection.

    Returns:
        list[dict] — sorted by exit_time ascending.
    """
    raw = _fetch_raw_trades(conn)
    if not raw:
        log.warning("build_canonical_dataset: no trades found in DB")
        return []

    groups = _group_by_symbol(raw)
    all_lifecycles: list[dict] = []

    for sym, rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: r.get("id") or 0)
        lcs = _match_lifecycles(rows_sorted)
        all_lifecycles.extend(lcs)

    # Sort by exit_time then entry_time
    all_lifecycles.sort(key=lambda r: (r.get("exit_time") or "", r.get("entry_time") or ""))

    log.info(
        "build_canonical_dataset: %d raw fills → %d completed lifecycles across %d symbols",
        len(raw), len(all_lifecycles), len(groups)
    )
    return all_lifecycles


def export_canonical_dataset_csv(conn, path: str) -> int:
    """
    Write the Canonical Trade Dataset to a CSV file.

    Args:
        conn: DB connection.
        path: Absolute path for the output CSV.

    Returns:
        Number of rows written.
    """
    import csv

    rows = build_canonical_dataset(conn)
    if not rows:
        log.warning("export_canonical_dataset_csv: no data to export")
        return 0

    # Drop internal debugging columns from the export
    export_keys = [k for k in rows[0].keys() if not k.startswith("_")]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=export_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    log.info("export_canonical_dataset_csv: wrote %d rows to %s", len(rows), path)
    return len(rows)
