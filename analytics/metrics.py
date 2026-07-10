"""
analytics/metrics.py — Edge Decomposition & Strategy Ranking

Computes performance analytics on the Canonical Trade Dataset.

Available reports:
    edge_report(lifecycles)       → dict  — aggregate performance metrics
    strategy_report(lifecycles)   → list[dict] — per-strategy ranking table
    print_edge_report(lifecycles) → None  — pretty-print to stdout
    print_strategy_report(...)    → None  — pretty-print ranked strategy table
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_div(num, denom, default=None):
    try:
        return num / denom if denom and denom != 0 else default
    except Exception:
        return default


def _try_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Core statistical functions
# ---------------------------------------------------------------------------

def _compute_edge_stats(pnls: list[float]) -> dict:
    """
    Compute core edge statistics for a list of per-trade PnL values.

    Returns:
        trade_count, win_count, loss_count, win_rate, avg_win, avg_loss,
        profit_factor, expectancy_per_trade, total_pnl, avg_pnl_per_trade
    """
    if not pnls:
        return {
            "trade_count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": None, "avg_win": None, "avg_loss": None,
            "profit_factor": None, "expectancy_per_trade": None,
            "total_pnl": 0.0, "avg_pnl_per_trade": None,
        }

    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_count  = len(wins)
    loss_count = len(losses)
    n          = len(pnls)

    win_rate        = win_count / n
    avg_win         = _safe_div(sum(wins),   win_count)
    avg_loss        = _safe_div(sum(losses), loss_count)  # negative number
    total_pnl       = sum(pnls)
    avg_pnl         = total_pnl / n

    # Profit Factor = gross profit / abs(gross loss)
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = _safe_div(gross_profit, gross_loss)

    # Expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    expectancy = None
    if avg_win is not None and avg_loss is not None:
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        "trade_count":          n,
        "win_count":            win_count,
        "loss_count":           loss_count,
        "win_rate":             round(win_rate, 4),
        "avg_win":              round(avg_win, 6)   if avg_win  is not None else None,
        "avg_loss":             round(avg_loss, 6)  if avg_loss is not None else None,
        "profit_factor":        round(profit_factor, 4) if profit_factor is not None else None,
        "expectancy_per_trade": round(expectancy, 6) if expectancy is not None else None,
        "total_pnl":            round(total_pnl, 6),
        "avg_pnl_per_trade":    round(avg_pnl, 6),
    }


def _compute_hold_stats(hold_seconds_list: list[float]) -> dict:
    valid = [s for s in hold_seconds_list if s is not None and s >= 0]
    if not valid:
        return {"avg_hold_seconds": None, "median_hold_seconds": None, "max_hold_seconds": None}
    valid.sort()
    n = len(valid)
    return {
        "avg_hold_seconds":    round(sum(valid) / n, 1),
        "median_hold_seconds": round(valid[n // 2], 1),
        "max_hold_seconds":    round(valid[-1], 1),
    }


def _compute_mfe_mae_stats(lifecycles: list[dict]) -> dict:
    """
    Summarise MFE (Maximum Favourable Excursion) and MAE (Maximum Adverse Excursion)
    for trades where lifecycle tracking was active.
    """
    live_lcs = [lc for lc in lifecycles if lc.get("mfe_mae_quality") == "live"]
    if not live_lcs:
        return {
            "mfe_mae_coverage_pct": 0.0,
            "avg_mfe": None, "avg_mae": None,
            "avg_mfe_pct": None, "avg_mae_pct": None,
        }

    mfes = [_try_float(lc.get("mfe")) for lc in live_lcs if lc.get("mfe") is not None]
    maes = [_try_float(lc.get("mae")) for lc in live_lcs if lc.get("mae") is not None]

    # MFE % relative to entry_vwap * entry_qty
    mfe_pcts = []
    for lc in live_lcs:
        mfe = _try_float(lc.get("mfe"))
        cost = _try_float(lc.get("entry_vwap"), 0) * _try_float(lc.get("entry_qty"), 0)
        if cost > 0 and mfe is not None:
            mfe_pcts.append(mfe / cost * 100)

    mae_pcts = []
    for lc in live_lcs:
        mae = _try_float(lc.get("mae"))
        cost = _try_float(lc.get("entry_vwap"), 0) * _try_float(lc.get("entry_qty"), 0)
        if cost > 0 and mae is not None:
            mae_pcts.append(mae / cost * 100)

    total = len(lifecycles)
    return {
        "mfe_mae_coverage_pct": round(len(live_lcs) / total * 100, 1) if total else 0.0,
        "avg_mfe":     round(sum(mfes) / len(mfes), 6)   if mfes else None,
        "avg_mae":     round(sum(maes) / len(maes), 6)   if maes else None,
        "avg_mfe_pct": round(sum(mfe_pcts) / len(mfe_pcts), 4) if mfe_pcts else None,
        "avg_mae_pct": round(sum(mae_pcts) / len(mae_pcts), 4) if mae_pcts else None,
    }


# ---------------------------------------------------------------------------
# Public report functions
# ---------------------------------------------------------------------------

def edge_report(lifecycles: list[dict]) -> dict[str, Any]:
    """
    Compute aggregate performance report across all completed trades.

    Args:
        lifecycles: Output of analytics.dataset.build_canonical_dataset()

    Returns:
        dict with keys:
            trade_count, win_count, loss_count, win_rate,
            avg_win, avg_loss, profit_factor, expectancy_per_trade,
            total_pnl, avg_pnl_per_trade,
            avg_hold_seconds, median_hold_seconds, max_hold_seconds,
            mfe_mae_coverage_pct, avg_mfe, avg_mae, avg_mfe_pct, avg_mae_pct,
            live_trade_count, shadow_trade_count
    """
    pnls = [_try_float(lc.get("realized_pnl")) for lc in lifecycles]
    holds = [lc.get("hold_seconds") for lc in lifecycles]

    stats = _compute_edge_stats(pnls)
    hold_stats = _compute_hold_stats(holds)
    mfe_stats = _compute_mfe_mae_stats(lifecycles)

    live_count   = sum(1 for lc in lifecycles if bool(lc.get("live")))
    shadow_count = sum(1 for lc in lifecycles if bool(lc.get("shadow_mode")))

    return {
        **stats,
        **hold_stats,
        **mfe_stats,
        "live_trade_count":   live_count,
        "shadow_trade_count": shadow_count,
    }


def strategy_report(lifecycles: list[dict]) -> list[dict[str, Any]]:
    """
    Compute per-strategy performance ranking.

    Returns a list of dicts sorted by expectancy_per_trade descending.
    Each dict contains all edge_report fields plus:
        strategy, regime (most common), symbol_count
    """
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for lc in lifecycles:
        s = str(lc.get("strategy") or "unknown")
        by_strategy[s].append(lc)

    rows = []
    for strategy, lcs in by_strategy.items():
        pnls  = [_try_float(lc.get("realized_pnl")) for lc in lcs]
        holds = [lc.get("hold_seconds") for lc in lcs]

        stats      = _compute_edge_stats(pnls)
        hold_stats = _compute_hold_stats(holds)
        mfe_stats  = _compute_mfe_mae_stats(lcs)

        symbols = {lc.get("symbol") for lc in lcs}

        # Most common regime for this strategy
        regime_counts: dict = defaultdict(int)
        for lc in lcs:
            r = lc.get("regime")
            if r:
                regime_counts[r] += 1
        dominant_regime = max(regime_counts, key=regime_counts.get) if regime_counts else None

        rows.append({
            "strategy":    strategy,
            "symbol_count": len(symbols),
            "dominant_regime": dominant_regime,
            **stats,
            **hold_stats,
            **mfe_stats,
        })

    # Sort: first by expectancy descending, then by trade_count descending
    rows.sort(
        key=lambda r: (
            r.get("expectancy_per_trade") or float("-inf"),
            r.get("trade_count") or 0,
        ),
        reverse=True,
    )
    return rows


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _fmt(v, fmt=".4f", none_str="N/A"):
    if v is None:
        return none_str
    try:
        return format(v, fmt)
    except Exception:
        return str(v)


def print_edge_report(lifecycles: list[dict], title: str = "AGGREGATE EDGE REPORT") -> None:
    """Print the aggregate edge report to stdout."""
    r = edge_report(lifecycles)
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)
    print(f"  Trades:             {r['trade_count']}  (live={r['live_trade_count']}, shadow={r['shadow_trade_count']})")
    print(f"  Win Rate:           {_fmt(r['win_rate'], '.2%')}")
    print(f"  Avg Win:            {_fmt(r['avg_win'])}")
    print(f"  Avg Loss:           {_fmt(r['avg_loss'])}")
    print(f"  Profit Factor:      {_fmt(r['profit_factor'])}")
    print(f"  Expectancy/trade:   {_fmt(r['expectancy_per_trade'])}")
    print(f"  Total PnL:          {_fmt(r['total_pnl'])}")
    print(f"  Avg Hold:           {_fmt(r['avg_hold_seconds'], '.0f')}s  (median {_fmt(r['median_hold_seconds'], '.0f')}s)")
    print(f"  MFE/MAE coverage:   {_fmt(r['mfe_mae_coverage_pct'], '.1f')}%")
    if r.get("avg_mfe") is not None:
        print(f"  Avg MFE:            {_fmt(r['avg_mfe'])} ({_fmt(r['avg_mfe_pct'], '.4f')}%)")
        print(f"  Avg MAE:            {_fmt(r['avg_mae'])} ({_fmt(r['avg_mae_pct'], '.4f')}%)")
    print(sep + "\n")


def print_strategy_report(lifecycles: list[dict]) -> None:
    """Print per-strategy ranking table to stdout."""
    rows = strategy_report(lifecycles)
    if not rows:
        print("No completed trades found.")
        return

    header = (
        f"{'Strategy':<40} {'Trades':>6} {'WinRate':>8} {'PF':>6} "
        f"{'Expect':>10} {'TotalPnL':>12} {'AvgHold':>9}"
    )
    sep = "-" * len(header)
    print(f"\n{'STRATEGY RANKING':^{len(header)}}")
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print(
            f"{str(r['strategy']):<40} "
            f"{r['trade_count']:>6} "
            f"{_fmt(r['win_rate'], '.1%'):>8} "
            f"{_fmt(r['profit_factor'], '.2f'):>6} "
            f"{_fmt(r['expectancy_per_trade'], '.4f'):>10} "
            f"{_fmt(r['total_pnl'], '.4f'):>12} "
            f"{_fmt(r['avg_hold_seconds'], '.0f'):>8}s"
        )
    print(sep + "\n")
