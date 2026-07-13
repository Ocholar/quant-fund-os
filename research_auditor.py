
#!/usr/bin/env python3
"""
Research Run Auditor — Quant Fund OS
=====================================
Strictly read-only. Consumes JSONL telemetry and produces research artifacts.

Inputs:
  logs/candidates/*.jsonl
  logs/trades/*.jsonl

Outputs:
  research/daily_research_report.md
  research/daily_research_metrics.json

Usage:
  python research_auditor.py [--date YYYY-MM-DD] [--all]

If --date is omitted, defaults to today.
If --all is specified, processes all available log dates.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
LOGS_CANDIDATES = ROOT / "logs" / "candidates"
LOGS_TRADES = ROOT / "logs" / "trades"
RESEARCH_DIR = ROOT / "research"

RESEARCH_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CandidateRecord:
    candidate_id: str
    cycle_id: Optional[int] = None
    symbol: Optional[str] = None
    rank: Optional[int] = None
    ranking_population: Optional[int] = None
    strength: Optional[float] = None
    momentum: Optional[float] = None
    volatility: Optional[float] = None
    confidence: Optional[float] = None
    regime: Optional[str] = None
    source: Optional[str] = None
    score_before_filters: Optional[float] = None
    decision: Optional[str] = None          # RANKED | FILTERED
    filter_reason: Optional[str] = None
    features: Optional[Dict] = None
    # filter events
    filter_events: List[Dict] = field(default_factory=list)
    approved: bool = False
    # linked trade
    trade_id: Optional[str] = None
    # timestamps
    ranked_at: Optional[str] = None


@dataclass
class TradeRecord:
    trade_id: str
    candidate_id: Optional[str] = None
    cycle_id: Optional[int] = None
    symbol: Optional[str] = None
    entry_price: Optional[float] = None
    position_size: Optional[float] = None
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    holding_time_seconds: Optional[float] = None
    exit_reason: Optional[str] = None
    R_multiple: Optional[float] = None
    MFE: Optional[float] = None
    MAE: Optional[float] = None
    fees_paid: Optional[float] = None
    opened_at: Optional[str] = None
    exited_at: Optional[str] = None
    is_complete: bool = False


@dataclass
class CycleSummary:
    cycle_id: int
    timestamp: Optional[str] = None
    total_candidates: int = 0
    candidates_above_threshold: int = 0
    filtered_count: int = 0
    approved_count: int = 0
    executed_count: int = 0
    regime: Optional[str] = None
    evaluation_time_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# JSONL Parser
# ---------------------------------------------------------------------------

def iter_jsonl(path: Path):
    """Yield parsed JSON objects from a JSONL file. Report malformed lines."""
    malformed = 0
    total = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            total += 1
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                malformed += 1
                if malformed <= 5:
                    print(f"  [WARN] Malformed JSON in {path.name}:{lineno}: {exc}", file=sys.stderr)
    if malformed:
        print(f"  [WARN] {malformed}/{total} malformed lines in {path.name}", file=sys.stderr)
    return malformed


def load_candidate_logs(target_date: Optional[str] = None) -> Tuple[
    Dict[str, CandidateRecord],  # keyed by candidate_id
    List[CycleSummary],
    int,  # malformed_count
]:
    candidates: Dict[str, CandidateRecord] = {}
    cycles: List[CycleSummary] = []
    malformed_total = 0

    files = sorted(LOGS_CANDIDATES.glob("candidates_*.jsonl"))
    if target_date:
        files = [f for f in files if target_date in f.name]

    for path in files:
        print(f"  Reading {path.name} ({path.stat().st_size // 1024 // 1024} MiB)…")
        for obj in iter_jsonl(path):
            etype = obj.get("event_type")
            payload = obj.get("payload", {})
            ts = obj.get("timestamp")

            if etype == "candidate_ranked":
                cid = payload.get("candidate_id")
                if not cid:
                    continue
                rec = candidates.setdefault(cid, CandidateRecord(candidate_id=cid))
                rec.cycle_id = payload.get("cycle_id")
                rec.symbol = payload.get("symbol")
                rec.rank = payload.get("rank")
                rec.ranking_population = payload.get("ranking_population")
                rec.strength = payload.get("strength")
                rec.momentum = payload.get("momentum")
                rec.volatility = payload.get("volatility")
                rec.confidence = payload.get("confidence")
                rec.regime = payload.get("regime")
                rec.source = payload.get("source")
                rec.score_before_filters = payload.get("score_before_filters")
                rec.decision = payload.get("decision")
                rec.filter_reason = payload.get("filter_reason")
                rec.features = payload.get("features")  # may be None for pre-patch events
                rec.ranked_at = ts

            elif etype == "candidate_filtered":
                cid = payload.get("candidate_id")
                if not cid:
                    continue
                rec = candidates.setdefault(cid, CandidateRecord(candidate_id=cid))
                rec.filter_events.append({
                    "reason": payload.get("reason"),
                    "filter_name": payload.get("filter_name"),
                    "raw_reason": payload.get("raw_reason"),
                    "filter_stage": payload.get("filter_stage"),
                    "terminal_filter": payload.get("terminal_filter"),
                    "details": payload.get("details"),
                    "ts": ts,
                })
                # Update symbol/cycle if not already known
                if not rec.symbol:
                    rec.symbol = payload.get("symbol")
                if rec.cycle_id is None:
                    rec.cycle_id = payload.get("cycle_id")

            elif etype == "candidate_approved":
                cid = payload.get("candidate_id")
                if cid:
                    rec = candidates.setdefault(cid, CandidateRecord(candidate_id=cid))
                    rec.approved = True
                    if not rec.symbol:
                        rec.symbol = payload.get("symbol")

            elif etype == "candidate_terminal":
                # Candidate reached a terminal state without execution (e.g. EXPIRED)
                cid = payload.get("candidate_id")
                if cid and cid in candidates:
                    candidates[cid].filter_events.append({
                        "reason": payload.get("state", "TERMINAL"),
                        "filter_name": "terminal_state",
                        "raw_reason": payload.get("reason"),
                        "filter_stage": 99,
                        "terminal_filter": "TERMINAL",
                        "details": {"state": payload.get("state")},
                        "ts": ts,
                    })

            elif etype == "cycle_summary":
                cycles.append(CycleSummary(
                    cycle_id=payload.get("cycle_id", 0),
                    timestamp=ts,
                    total_candidates=payload.get("total_candidates", 0),
                    candidates_above_threshold=payload.get("candidates_above_threshold", 0),
                    filtered_count=payload.get("filtered_count", 0),
                    approved_count=payload.get("approved_count", 0),
                    executed_count=payload.get("executed_count", 0),
                    regime=payload.get("regime"),
                    evaluation_time_ms=payload.get("evaluation_time_ms"),
                ))

    return candidates, cycles, malformed_total


def load_trade_logs(target_date: Optional[str] = None) -> Tuple[
    Dict[str, TradeRecord],  # keyed by trade_id
    int,  # malformed_count
]:
    trades: Dict[str, TradeRecord] = {}
    malformed_total = 0

    files = sorted(LOGS_TRADES.glob("trades_*.jsonl"))
    if target_date:
        # Include all files — trades opened before the date may close later
        files = [f for f in files]

    for path in files:
        print(f"  Reading {path.name}…")
        for obj in iter_jsonl(path):
            etype = obj.get("event_type")
            payload = obj.get("payload", {})
            ts = obj.get("timestamp")

            if etype in ("trade_executed", "trade_execution_started"):
                tid = payload.get("trade_id")
                if not tid:
                    continue
                rec = trades.setdefault(tid, TradeRecord(trade_id=tid))
                rec.candidate_id = payload.get("candidate_id")
                rec.cycle_id = payload.get("cycle_id")
                rec.symbol = payload.get("symbol")
                rec.entry_price = payload.get("entry_price")
                rec.position_size = payload.get("position_size")
                rec.opened_at = ts

            elif etype == "trade_open":
                tid = payload.get("trade_id")
                if tid:
                    rec = trades.setdefault(tid, TradeRecord(trade_id=tid))
                    rec.candidate_id = payload.get("candidate_id") or rec.candidate_id
                    rec.symbol = payload.get("symbol") or rec.symbol
                    if not rec.opened_at:
                        rec.opened_at = ts

            elif etype == "trade_exited":
                tid = payload.get("trade_id")
                if not tid:
                    continue
                rec = trades.setdefault(tid, TradeRecord(trade_id=tid))
                rec.candidate_id = payload.get("candidate_id") or rec.candidate_id
                rec.symbol = payload.get("symbol") or rec.symbol
                rec.exit_price = payload.get("exit_price")
                rec.realized_pnl = payload.get("realized_pnl")
                rec.holding_time_seconds = payload.get("holding_time_seconds")
                rec.exit_reason = payload.get("exit_reason")
                rec.R_multiple = payload.get("R_multiple")
                rec.MFE = payload.get("MFE")
                rec.MAE = payload.get("MAE")
                rec.fees_paid = payload.get("fees_paid")
                rec.exited_at = ts
                rec.is_complete = True

    return trades, malformed_total


# ---------------------------------------------------------------------------
# Linkage & Reconciliation
# ---------------------------------------------------------------------------

def reconcile(
    candidates: Dict[str, CandidateRecord],
    trades: Dict[str, TradeRecord],
) -> Dict:
    """Join trades to candidates via candidate_id. Report orphans."""

    # Build candidate_id -> trade_id map
    cid_to_trade: Dict[str, str] = {}
    for tid, tr in trades.items():
        if tr.candidate_id:
            if tr.candidate_id in cid_to_trade:
                pass  # duplicate — will be reported
            cid_to_trade[tr.candidate_id] = tid

    linked_candidates = 0
    linked_trades = 0
    orphan_candidates = 0   # approved but no trade
    orphan_trades = 0       # trade with no matching candidate
    duplicate_cids = 0

    # Link trades to candidates
    seen_cids = collections.Counter()
    for tid, tr in trades.items():
        cid = tr.candidate_id
        if cid:
            seen_cids[cid] += 1
            if cid in candidates:
                candidates[cid].trade_id = tid
                linked_trades += 1
            else:
                orphan_trades += 1
        else:
            orphan_trades += 1

    duplicate_cids = sum(1 for c, n in seen_cids.items() if n > 1)

    # Count approved candidates without trades
    for cid, rec in candidates.items():
        if rec.approved:
            if cid in cid_to_trade:
                linked_candidates += 1
            else:
                orphan_candidates += 1

    return {
        "candidates_processed": len(candidates),
        "candidates_approved": sum(1 for r in candidates.values() if r.approved),
        "candidates_linked_to_trade": linked_candidates,
        "trades_total": len(trades),
        "trades_linked_to_candidate": linked_trades,
        "orphan_candidates": orphan_candidates,
        "orphan_trades": orphan_trades,
        "duplicate_candidate_ids": duplicate_cids,
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _safe_mean(vals):
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None

def _safe_median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None

def _safe_stdev(vals):
    vals = [v for v in vals if v is not None]
    return statistics.stdev(vals) if len(vals) >= 2 else None


def compute_runtime_summary(cycles: List[CycleSummary]) -> Dict:
    if not cycles:
        return {}
    # Sort by cycle_id
    cycles_sorted = sorted(cycles, key=lambda c: c.cycle_id)
    first_ts = cycles_sorted[0].timestamp
    last_ts = cycles_sorted[-1].timestamp
    min_cycle = cycles_sorted[0].cycle_id
    max_cycle = cycles_sorted[-1].cycle_id
    total_cycles = len(cycles)

    # Detect stalls: consecutive cycle_ids with large gaps
    cids = [c.cycle_id for c in cycles_sorted]
    gaps = [cids[i+1] - cids[i] for i in range(len(cids)-1)]
    stalls = sum(1 for g in gaps if g > 5)

    return {
        "first_event": first_ts,
        "last_event": last_ts,
        "cycle_id_range": [min_cycle, max_cycle],
        "total_cycle_summaries": total_cycles,
        "stalls_detected": stalls,
        "regimes_observed": list({c.regime for c in cycles if c.regime}),
        "avg_evaluation_time_ms": _safe_mean([c.evaluation_time_ms for c in cycles]),
    }


def compute_candidate_funnel(cycles: List[CycleSummary], candidates: Dict[str, CandidateRecord]) -> Dict:
    total_candidates = sum(c.total_candidates for c in cycles)
    ranked = sum(1 for r in candidates.values() if r.rank is not None)
    filtered_pre = sum(1 for r in candidates.values() if r.decision == "FILTERED")
    approved = sum(1 for r in candidates.values() if r.approved)
    trades_linked = sum(1 for r in candidates.values() if r.trade_id is not None)
    return {
        "total_candidates": total_candidates,
        "ranked": ranked,
        "pre_filter_rejected": filtered_pre,
        "approved": approved,
        "trades_executed": trades_linked,
    }


def compute_reject_analysis(candidates: Dict[str, CandidateRecord]) -> List[Dict]:
    reason_counter: collections.Counter = collections.Counter()
    for rec in candidates.values():
        for fe in rec.filter_events:
            reason = fe.get("reason") or fe.get("filter_name") or "UNKNOWN"
            reason_counter[reason] += 1
    total = sum(reason_counter.values())
    result = []
    for reason, count in reason_counter.most_common():
        result.append({
            "reason": reason,
            "count": count,
            "pct": round(100 * count / total, 1) if total else 0,
        })
    return result


def compute_trading_performance(trades: Dict[str, TradeRecord]) -> Dict:
    completed = [t for t in trades.values() if t.is_complete and t.realized_pnl is not None]
    if not completed:
        return {"note": "No completed trades with PnL data found."}

    pnls = [t.realized_pnl for t in completed]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls) if pnls else None
    expectancy = _safe_mean(pnls)
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    holding_times = [t.holding_time_seconds for t in completed if t.holding_time_seconds is not None]
    r_multiples = [t.R_multiple for t in completed if t.R_multiple is not None]

    # Sharpe (if enough data)
    sharpe = None
    if len(pnls) >= 5:
        mean_pnl = _safe_mean(pnls)
        std_pnl = _safe_stdev(pnls)
        if std_pnl and std_pnl > 0:
            sharpe = mean_pnl / std_pnl

    # Drawdown
    cumulative = []
    total = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        total += p
        peak = max(peak, total)
        dd = peak - total
        max_dd = max(max_dd, dd)

    return {
        "completed_trades": len(completed),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "expectancy": round(expectancy, 4) if expectancy is not None else None,
        "avg_R": round(_safe_mean(r_multiples), 4) if r_multiples else "Insufficient data",
        "avg_holding_time_seconds": round(_safe_mean(holding_times), 1) if holding_times else None,
        "realized_pnl_total": round(sum(pnls), 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else "Insufficient data (need ≥5 trades)",
        "exit_reasons": dict(collections.Counter(t.exit_reason for t in completed if t.exit_reason)),
    }


def compute_signal_analysis(candidates: Dict[str, CandidateRecord], trades: Dict[str, TradeRecord]) -> Dict:
    strengths = [r.strength for r in candidates.values() if r.strength is not None]
    if not strengths:
        return {"note": "No signal data available."}

    # Signal for winners / losers
    winner_signals = []
    loser_signals = []
    for rec in candidates.values():
        if rec.trade_id and rec.trade_id in trades:
            tr = trades[rec.trade_id]
            if tr.realized_pnl is not None and rec.strength is not None:
                if tr.realized_pnl > 0:
                    winner_signals.append(rec.strength)
                else:
                    loser_signals.append(rec.strength)

    return {
        "avg_signal": round(_safe_mean(strengths), 5) if strengths else None,
        "median_signal": round(_safe_median(strengths), 5) if strengths else None,
        "min_signal": round(min(strengths), 5),
        "max_signal": round(max(strengths), 5),
        "avg_signal_winners": round(_safe_mean(winner_signals), 5) if winner_signals else "Insufficient data",
        "avg_signal_losers": round(_safe_mean(loser_signals), 5) if loser_signals else "Insufficient data",
    }


def compute_ranking_analysis(candidates: Dict[str, CandidateRecord], trades: Dict[str, TradeRecord]) -> Dict:
    ranked_recs = [r for r in candidates.values() if r.rank is not None]
    if not ranked_recs:
        return {"note": "No ranked candidate data."}

    ranks = [r.rank for r in ranked_recs]

    # rank distribution buckets
    rank_dist: collections.Counter = collections.Counter()
    for r in ranks:
        bucket = f"rank_{r}" if r <= 5 else "rank_6+"
        rank_dist[bucket] += 1

    # top-ranked winners/losers
    top_winners = []
    top_losers = []
    for rec in ranked_recs:
        if rec.trade_id and rec.trade_id in trades:
            tr = trades[rec.trade_id]
            if tr.realized_pnl is not None:
                entry = {"symbol": rec.symbol, "rank": rec.rank, "pnl": tr.realized_pnl}
                if tr.realized_pnl > 0:
                    top_winners.append(entry)
                else:
                    top_losers.append(entry)

    top_winners.sort(key=lambda x: x["rank"])
    top_losers.sort(key=lambda x: x["rank"])

    return {
        "avg_rank": round(_safe_mean(ranks), 2),
        "rank_distribution": dict(rank_dist),
        "highest_ranked_winners": top_winners[:5],
        "highest_ranked_losers": top_losers[:5],
    }


def compute_regime_analysis(candidates: Dict[str, CandidateRecord], trades: Dict[str, TradeRecord]) -> Dict:
    by_regime: Dict[str, List] = collections.defaultdict(list)
    for rec in candidates.values():
        if rec.regime and rec.trade_id and rec.trade_id in trades:
            tr = trades[rec.trade_id]
            by_regime[rec.regime].append(tr)

    result = {}
    for regime, trade_list in by_regime.items():
        completed = [t for t in trade_list if t.is_complete and t.realized_pnl is not None]
        pnls = [t.realized_pnl for t in completed]
        win_rate = (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None
        signals = [
            candidates[t.candidate_id].strength
            for t in trade_list
            if t.candidate_id in candidates and candidates[t.candidate_id].strength is not None
        ]
        result[regime] = {
            "trades": len(completed),
            "expectancy": round(_safe_mean(pnls), 4) if pnls else "No data",
            "win_rate": round(win_rate, 4) if win_rate is not None else "No data",
            "avg_signal": round(_safe_mean(signals), 5) if signals else "No data",
        }
    return result


def compute_feature_correlation(
    candidates: Dict[str, CandidateRecord],
    trades: Dict[str, TradeRecord],
) -> Dict:
    """Simple Pearson correlation between each feature and realized PnL outcome."""
    FEATURES = [
        "trend", "long_trend", "one_tick_momentum", "symbol_trend_score",
        "breakout_score", "trend_quality", "is_symbol_uptrend",
        "is_symbol_downtrend", "is_choppy",
    ]

    feat_pnl: Dict[str, List[Tuple[float, float]]] = {f: [] for f in FEATURES}

    for rec in candidates.values():
        if not rec.features or not rec.trade_id:
            continue
        tr = trades.get(rec.trade_id)
        if not tr or tr.realized_pnl is None:
            continue
        pnl = tr.realized_pnl
        for feat in FEATURES:
            val = rec.features.get(feat)
            if val is not None:
                try:
                    feat_pnl[feat].append((float(val), float(pnl)))
                except (TypeError, ValueError):
                    pass

    def pearson(pairs):
        if len(pairs) < 3:
            return None
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = _safe_mean(xs), _safe_mean(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        denom = math.sqrt(sum((x - mx)**2 for x in xs) * sum((y - my)**2 for y in ys))
        return round(num / denom, 4) if denom > 0 else None

    result = {}
    for feat in FEATURES:
        pairs = feat_pnl[feat]
        corr = pearson(pairs)
        n = len(pairs)
        result[feat] = {
            "n": n,
            "pearson_r": corr if corr is not None else "Insufficient data",
            "note": "Descriptive only. No predictive model." if corr is not None else "Fewer than 3 linked observations.",
        }
    return result


def compute_filter_effectiveness(
    candidates: Dict[str, CandidateRecord],
    trades: Dict[str, TradeRecord],
) -> Dict:
    """
    For each reject reason: how many rejected? How many would have been profitable?
    Note: without counterfactual data this is evidence-bounded.
    """
    # We can only analyze filters that produced candidates with a LATER trade
    # (i.e., same symbol in same session). This is a very conservative estimate.
    by_reason: Dict[str, Dict] = {}

    # Collect all profitable exits
    profitable_symbols = {
        t.symbol for t in trades.values()
        if t.is_complete and t.realized_pnl is not None and t.realized_pnl > 0
    }
    unprofitable_symbols = {
        t.symbol for t in trades.values()
        if t.is_complete and t.realized_pnl is not None and t.realized_pnl <= 0
    }

    for rec in candidates.values():
        if not rec.filter_events:
            continue
        for fe in rec.filter_events:
            reason = fe.get("reason") or fe.get("filter_name") or "UNKNOWN"
            if reason not in by_reason:
                by_reason[reason] = {
                    "rejected_count": 0,
                    "symbol_later_profitable": 0,
                    "symbol_later_unprofitable": 0,
                    "evidence_note": "",
                }
            by_reason[reason]["rejected_count"] += 1
            sym = rec.symbol
            if sym in profitable_symbols:
                by_reason[reason]["symbol_later_profitable"] += 1
            if sym in unprofitable_symbols:
                by_reason[reason]["symbol_later_unprofitable"] += 1

    for reason, data in by_reason.items():
        total_sym = data["symbol_later_profitable"] + data["symbol_later_unprofitable"]
        if total_sym == 0:
            data["evidence_note"] = "Insufficient evidence — no same-session trade data for rejected symbols."
        else:
            data["evidence_note"] = (
                f"Of {data['rejected_count']} rejected, {data['symbol_later_profitable']} "
                f"({round(100*data['symbol_later_profitable']/data['rejected_count'],1)}%) "
                f"had a later profitable trade on the same symbol in session. "
                f"Caution: same-symbol proxy only; not a counterfactual."
            )

    return by_reason


def compute_top_findings(
    trading: Dict,
    signal: Dict,
    feature_corr: Dict,
    reject_analysis: List[Dict],
    filter_eff: Dict,
) -> List[str]:
    findings = []

    # Finding 1: win rate
    wr = trading.get("win_rate")
    if isinstance(wr, float):
        findings.append(
            f"Completed trade win rate is {wr:.1%}. "
            f"Expectancy: {trading.get('expectancy', 'N/A')} per trade."
        )

    # Finding 2: top reject reason
    if reject_analysis:
        top = reject_analysis[0]
        findings.append(
            f"Dominant reject reason: '{top['reason']}' ({top['count']} rejections, {top['pct']}% of all filter events)."
        )

    # Finding 3: feature with highest absolute correlation
    valid_corr = {
        f: d["pearson_r"] for f, d in feature_corr.items()
        if isinstance(d.get("pearson_r"), float)
    }
    if valid_corr:
        best_feat = max(valid_corr, key=lambda x: abs(valid_corr[x]))
        findings.append(
            f"Feature '{best_feat}' shows highest absolute Pearson correlation with realized PnL "
            f"(r={valid_corr[best_feat]:.4f}, n={feature_corr[best_feat]['n']}). "
            f"Descriptive only."
        )

    # Finding 4: signal distribution
    avg_s = signal.get("avg_signal")
    if avg_s is not None:
        findings.append(
            f"Average signal strength across all candidates: {avg_s:.5f}. "
            f"Range: [{signal.get('min_signal')}, {signal.get('max_signal')}]."
        )

    # Finding 5: filter effectiveness (top reason)
    if filter_eff:
        top_fe_reason = max(filter_eff, key=lambda r: filter_eff[r]["rejected_count"])
        d = filter_eff[top_fe_reason]
        if d.get("evidence_note"):
            findings.append(f"Filter effectiveness — {top_fe_reason}: {d['evidence_note']}")

    return findings[:5]


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

def _fmt(val, fmt=".4f", fallback="N/A"):
    if val is None or val == "":
        return fallback
    if isinstance(val, float):
        return format(val, fmt)
    return str(val)


def generate_markdown_report(
    target_date: str,
    runtime: Dict,
    funnel: Dict,
    recon: Dict,
    reject: List[Dict],
    trading: Dict,
    signal: Dict,
    ranking: Dict,
    regime: Dict,
    feat_corr: Dict,
    filter_eff: Dict,
    findings: List[str],
) -> str:
    lines = []
    lines.append(f"# Daily Research Report — {target_date}")
    lines.append(f"*Generated: {datetime.now(timezone.utc).isoformat()}*")
    lines.append("")

    # 1. Runtime Summary
    lines.append("## 1. Runtime Summary")
    lines.append(f"| Field | Value |")
    lines.append(f"|-------|-------|")
    lines.append(f"| First event | {runtime.get('first_event', 'N/A')} |")
    lines.append(f"| Last event | {runtime.get('last_event', 'N/A')} |")
    lines.append(f"| Cycle ID range | {runtime.get('cycle_id_range', 'N/A')} |")
    lines.append(f"| Total cycle summaries | {runtime.get('total_cycle_summaries', 'N/A')} |")
    lines.append(f"| Stalls detected (cycle gaps > 5) | {runtime.get('stalls_detected', 'N/A')} |")
    lines.append(f"| Regimes observed | {', '.join(runtime.get('regimes_observed', []))} |")
    lines.append(f"| Avg evaluation time (ms) | {_fmt(runtime.get('avg_evaluation_time_ms'), '.2f')} |")
    lines.append("")

    # 2. Reconciliation
    lines.append("## 2. Reconciliation Statistics")
    lines.append(f"| Field | Count |")
    lines.append(f"|-------|-------|")
    for k, v in recon.items():
        lines.append(f"| {k.replace('_', ' ').title()} | {v} |")
    lines.append("")

    # 3. Candidate Funnel
    lines.append("## 3. Candidate Funnel")
    lines.append("```")
    lines.append(f"  Total candidates evaluated : {funnel.get('total_candidates', 0):>8}")
    lines.append(f"  Ranked (passed pre-filter)  : {funnel.get('ranked', 0):>8}")
    lines.append(f"  Rejected pre-filter         : {funnel.get('pre_filter_rejected', 0):>8}")
    lines.append(f"  Approved by allocator       : {funnel.get('approved', 0):>8}")
    lines.append(f"  Trades executed             : {funnel.get('trades_executed', 0):>8}")
    lines.append("```")
    lines.append("")

    # 4. Reject Analysis
    lines.append("## 4. Reject Analysis")
    if reject:
        lines.append("| Reason | Count | % |")
        lines.append("|--------|-------|---|")
        for r in reject:
            lines.append(f"| {r['reason']} | {r['count']} | {r['pct']}% |")
    else:
        lines.append("*No filter events recorded.*")
    lines.append("")

    # 5. Trading Performance
    lines.append("## 5. Trading Performance")
    if "note" in trading:
        lines.append(f"*{trading['note']}*")
    else:
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Completed trades | {trading.get('completed_trades', 'N/A')} |")
        lines.append(f"| Win rate | {_fmt(trading.get('win_rate'), '.1%')} |")
        lines.append(f"| Expectancy (per trade) | {_fmt(trading.get('expectancy'))} |")
        lines.append(f"| Average R-multiple | {trading.get('avg_R', 'N/A')} |")
        lines.append(f"| Avg holding time (s) | {_fmt(trading.get('avg_holding_time_seconds'), '.1f')} |")
        lines.append(f"| Realized PnL (total) | {_fmt(trading.get('realized_pnl_total'))} |")
        lines.append(f"| Gross profit | {_fmt(trading.get('gross_profit'))} |")
        lines.append(f"| Gross loss | {_fmt(trading.get('gross_loss'))} |")
        lines.append(f"| Max drawdown | {_fmt(trading.get('max_drawdown'))} |")
        lines.append(f"| Profit factor | {_fmt(trading.get('profit_factor'))} |")
        lines.append(f"| Sharpe ratio | {trading.get('sharpe_ratio', 'N/A')} |")
        if trading.get("exit_reasons"):
            lines.append("")
            lines.append("**Exit reasons:**")
            for reason, cnt in trading["exit_reasons"].items():
                lines.append(f"- {reason}: {cnt}")
    lines.append("")

    # 6. Signal Analysis
    lines.append("## 6. Signal Analysis")
    if "note" in signal:
        lines.append(f"*{signal['note']}*")
    else:
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Average signal | {_fmt(signal.get('avg_signal'), '.5f')} |")
        lines.append(f"| Median signal | {_fmt(signal.get('median_signal'), '.5f')} |")
        lines.append(f"| Min signal | {_fmt(signal.get('min_signal'), '.5f')} |")
        lines.append(f"| Max signal | {_fmt(signal.get('max_signal'), '.5f')} |")
        lines.append(f"| Avg signal (winners) | {signal.get('avg_signal_winners', 'N/A')} |")
        lines.append(f"| Avg signal (losers) | {signal.get('avg_signal_losers', 'N/A')} |")
    lines.append("")

    # 7. Ranking Analysis
    lines.append("## 7. Ranking Analysis")
    if "note" in ranking:
        lines.append(f"*{ranking['note']}*")
    else:
        lines.append(f"- Average candidate rank: {ranking.get('avg_rank', 'N/A')}")
        lines.append(f"- Rank distribution: {ranking.get('rank_distribution', {})}")
        if ranking.get("highest_ranked_winners"):
            lines.append("\n**Top-ranked winning trades:**")
            for t in ranking["highest_ranked_winners"]:
                lines.append(f"  - {t['symbol']} rank={t['rank']} pnl={t['pnl']:.4f}")
        if ranking.get("highest_ranked_losers"):
            lines.append("\n**Top-ranked losing trades:**")
            for t in ranking["highest_ranked_losers"]:
                lines.append(f"  - {t['symbol']} rank={t['rank']} pnl={t['pnl']:.4f}")
    lines.append("")

    # 8. Regime Analysis
    lines.append("## 8. Regime Analysis")
    if not regime:
        lines.append("*No regime-linked trade data.*")
    else:
        lines.append("| Regime | Trades | Win Rate | Expectancy | Avg Signal |")
        lines.append("|--------|--------|----------|------------|------------|")
        for reg, d in regime.items():
            lines.append(
                f"| {reg} | {d['trades']} | {d['win_rate']} | {d['expectancy']} | {d['avg_signal']} |"
            )
    lines.append("")

    # 9. Feature Correlation
    lines.append("## 9. Feature Correlation (Descriptive)")
    lines.append("*Simple Pearson r between feature value and realized PnL. No predictive model.*")
    lines.append("")
    lines.append("| Feature | n | Pearson r |")
    lines.append("|---------|---|-----------|")
    for feat, d in feat_corr.items():
        lines.append(f"| {feat} | {d['n']} | {d['pearson_r']} |")
    lines.append("")

    # 10. Filter Effectiveness
    lines.append("## 10. Filter Effectiveness")
    lines.append("*Evidence-bounded estimate only. Not a counterfactual.*")
    lines.append("")
    if not filter_eff:
        lines.append("*No filter data.*")
    else:
        for reason, d in sorted(filter_eff.items(), key=lambda x: -x[1]["rejected_count"]):
            lines.append(f"### {reason}")
            lines.append(f"- Rejected: {d['rejected_count']}")
            lines.append(f"- Symbol later profitable (proxy): {d['symbol_later_profitable']}")
            lines.append(f"- Symbol later unprofitable (proxy): {d['symbol_later_unprofitable']}")
            lines.append(f"- {d['evidence_note']}")
            lines.append("")

    # 11. Top Findings
    lines.append("## 11. Top Findings")
    if findings:
        for i, f in enumerate(findings, 1):
            lines.append(f"{i}. {f}")
    else:
        lines.append("*Insufficient data for evidence-backed findings.*")
    lines.append("")

    return "\n".join(lines)


def generate_metrics_json(
    target_date: str,
    runtime: Dict,
    funnel: Dict,
    recon: Dict,
    reject: List[Dict],
    trading: Dict,
    signal: Dict,
    ranking: Dict,
    regime: Dict,
    feat_corr: Dict,
    filter_eff: Dict,
    findings: List[str],
) -> Dict:
    return {
        "report_date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_summary": runtime,
        "reconciliation": recon,
        "candidate_funnel": funnel,
        "reject_analysis": reject,
        "trading_performance": trading,
        "signal_analysis": signal,
        "ranking_analysis": ranking,
        "regime_analysis": regime,
        "feature_correlation": feat_corr,
        "filter_effectiveness": {r: d for r, d in filter_eff.items()},
        "top_findings": findings,
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def run_audit(target_date: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Research Run Auditor — {target_date}")
    print(f"{'='*60}")

    print("\n[1/6] Loading candidate logs…")
    candidates, cycles, _ = load_candidate_logs(target_date)
    print(f"      {len(candidates)} candidate records, {len(cycles)} cycle summaries")

    print("\n[2/6] Loading trade logs…")
    trades, _ = load_trade_logs(target_date)
    print(f"      {len(trades)} trade records ({sum(1 for t in trades.values() if t.is_complete)} complete)")

    print("\n[3/6] Reconciling…")
    recon = reconcile(candidates, trades)
    print(f"      Linked: {recon['candidates_linked_to_trade']} candidates -> trades")
    print(f"      Orphan candidates: {recon['orphan_candidates']}, orphan trades: {recon['orphan_trades']}")

    print("\n[4/6] Computing analytics…")
    runtime  = compute_runtime_summary(cycles)
    funnel   = compute_candidate_funnel(cycles, candidates)
    reject   = compute_reject_analysis(candidates)
    trading  = compute_trading_performance(trades)
    signal   = compute_signal_analysis(candidates, trades)
    ranking  = compute_ranking_analysis(candidates, trades)
    regime   = compute_regime_analysis(candidates, trades)
    feat_corr = compute_feature_correlation(candidates, trades)
    filter_eff = compute_filter_effectiveness(candidates, trades)
    findings = compute_top_findings(trading, signal, feat_corr, reject, filter_eff)

    print("\n[5/6] Writing reports…")
    report_md = generate_markdown_report(
        target_date, runtime, funnel, recon, reject,
        trading, signal, ranking, regime, feat_corr, filter_eff, findings
    )
    metrics = generate_metrics_json(
        target_date, runtime, funnel, recon, reject,
        trading, signal, ranking, regime, feat_corr, filter_eff, findings
    )

    md_path = RESEARCH_DIR / f"daily_research_report_{target_date}.md"
    json_path = RESEARCH_DIR / f"daily_research_metrics_{target_date}.json"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    print(f"      {md_path}")
    print(f"      {json_path}")

    # Determinism check — rerun and compare
    print("\n[6/6] Determinism check (2nd pass)…")
    metrics2 = generate_metrics_json(
        target_date, runtime, funnel, recon, reject,
        trading, signal, ranking, regime, feat_corr, filter_eff, findings
    )
    j1 = json.dumps({k: v for k, v in metrics.items() if k != "generated_at"}, sort_keys=True, default=str)
    j2 = json.dumps({k: v for k, v in metrics2.items() if k != "generated_at"}, sort_keys=True, default=str)
    if j1 == j2:
        print("      OK Deterministic: two passes produce identical output (excluding wall-clock timestamp).")
    else:
        print("      FAIL WARNING: non-deterministic output detected.")

    print(f"\n{'='*60}")
    print("  Audit complete.")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Quant Fund OS — Research Run Auditor")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--all", action="store_true", help="Process all available dates")
    args = parser.parse_args()

    if args.all:
        dates = sorted({
            f.stem.replace("candidates_", "")
            for f in LOGS_CANDIDATES.glob("candidates_*.jsonl")
        })
        for d in dates:
            run_audit(d)
    else:
        target = args.date or date.today().isoformat()
        run_audit(target)


if __name__ == "__main__":
    main()
