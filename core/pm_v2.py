"""
Portfolio Manager V2 -- DRY RUN OBSERVER
==========================================

Stage 1: Observation only. Zero impact on portfolio, orders, or exits.

PM_V2_ENABLED controls whether the module runs at all.
PM_V2_DRY_RUN must be True for Stage 1. Setting it False is Stage 2 (not yet approved).

Decision rule (identical to production rule):
  IF portfolio_full (incoming Rank 1 was capacity-rejected)
  AND incoming.score > weakest_open.entry_score + PM_V2_SCORE_MARGIN
  AND weakest_open.age_minutes >= PM_V2_MIN_AGE_MINUTES
  THEN replacement_would_fire = True

No exits. No orders. No portfolio mutations. Logging only.

Log files:
  logs/pm_v2/pm_v2_candidates.jsonl   -- dry-run replacement candidates
  logs/pm_v2/pm_v2_outcomes.jsonl     -- ex-post realized PnL comparison
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from core.config import settings

# ── Configuration ────────────────────────────────────────────────────────────
PM_V2_MIN_AGE_MINUTES = 15.0   # Only consider evicting incumbents older than this.
PM_V2_SCORE_MARGIN    = 0.001  # Candidate score must exceed incumbent by at least this.
PM_V2_MAX_PER_DAY     = 2      # Future Stage 2 cap. Unused in dry-run.

_LOG_DIR  = Path("logs/pm_v2")
_CAND_LOG = _LOG_DIR / "pm_v2_candidates.jsonl"
_OUT_LOG  = _LOG_DIR / "pm_v2_outcomes.jsonl"
_LOG_LOCK = Lock()

# ── Internal helpers ─────────────────────────────────────────────────────────

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        r = float(v)
        return default if (math.isnan(r) or math.isinf(r)) else r
    except Exception:
        return default

def _ensure_log_dir() -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _append_log(path: Path, record: Dict[str, Any]) -> None:
    _ensure_log_dir()
    with _LOG_LOCK:
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        except Exception as exc:
            print(f"[PM_V2] log_error path={path} err={exc!r}", flush=True)


def _get_open_positions_with_scores(engine) -> list[Dict[str, Any]]:
    """
    Query all open positions from the DB.
    Returns list of dicts with: symbol, qty, entry_time, entry_score (nullable).

    entry_score is fetched from the pm_v2_entry_scores table if it exists,
    otherwise falls back to the confidence column on the trade record.

    This function never raises -- any failure returns an empty list.
    """
    try:
        from sqlalchemy import text as _text
        with engine.begin() as conn:
            rows = conn.execute(_text("""
                SELECT
                    p.symbol,
                    p.quantity,
                    t.created_at          AS entry_time,
                    t.confidence          AS entry_confidence,
                    COALESCE(s.score, 0)  AS entry_score
                FROM positions p
                LEFT JOIN trades t
                    ON t.symbol = p.symbol
                    AND LOWER(t.side) = 'buy'
                    AND t.quantity > 0
                    AND t.created_at = (
                        SELECT MAX(t2.created_at)
                        FROM trades t2
                        WHERE t2.symbol = p.symbol
                          AND LOWER(t2.side) = 'buy'
                          AND t2.quantity > 0
                    )
                LEFT JOIN pm_v2_entry_scores s
                    ON s.symbol = p.symbol
                    AND s.trade_uuid = t.trade_uuid
                WHERE p.quantity > 0
                ORDER BY t.created_at ASC
            """)).mappings().fetchall()
            return [dict(r) for r in rows]
    except Exception:
        # pm_v2_entry_scores table may not exist yet -- use fallback query
        try:
            from sqlalchemy import text as _text
            with engine.begin() as conn:
                rows = conn.execute(_text("""
                    SELECT
                        p.symbol,
                        p.quantity,
                        t.created_at     AS entry_time,
                        t.confidence     AS entry_confidence,
                        t.confidence     AS entry_score
                    FROM positions p
                    LEFT JOIN trades t
                        ON t.symbol = p.symbol
                        AND LOWER(t.side) = 'buy'
                        AND t.quantity > 0
                        AND t.created_at = (
                            SELECT MAX(t2.created_at)
                            FROM trades t2
                            WHERE t2.symbol = p.symbol
                              AND LOWER(t2.side) = 'buy'
                              AND t2.quantity > 0
                        )
                    WHERE p.quantity > 0
                    ORDER BY t.created_at ASC
                """)).mappings().fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []


# ── Public API ───────────────────────────────────────────────────────────────

def pm_v2_on_capacity_rejection(
    *,
    incoming_symbol:  str,
    incoming_score:   float,
    incoming_rank:    int,
    rejection_reason: str,
    engine,
) -> None:
    """
    Called when a Rank 1 candidate is rejected due to a capacity constraint
    (MAX_POSITIONS, MAX_EXPOSURE, etc.).

    Stage 1: logs a structured pm_v2_candidate event.
    Stage 2+: (not yet active) would queue an eviction order.

    This function is NEVER allowed to raise an exception to the caller.
    Any failure is silently swallowed after logging.
    """
    if not settings.pm_v2_enabled:
        return

    try:
        _pm_v2_observe(
            incoming_symbol=incoming_symbol,
            incoming_score=_safe_float(incoming_score),
            incoming_rank=int(incoming_rank),
            rejection_reason=str(rejection_reason),
            engine=engine,
        )
    except Exception as exc:
        try:
            print(f"[PM_V2] observer_error err={exc!r}", flush=True)
        except Exception:
            pass


def pm_v2_on_trade_closed(
    *,
    symbol:    str,
    trade_uuid: str,
    side:       str,
    pnl:        float,
    engine,
) -> None:
    """
    Called after any SELL trade is persisted.

    Looks up pending dry-run candidate records that targeted this symbol
    and writes a pm_v2_outcome event comparing the candidate's eventual PnL
    vs the retained incumbent's eventual PnL.

    Stage 1: log only. No portfolio effect.
    """
    if not settings.pm_v2_enabled:
        return
    if str(side).lower() != "sell":
        return

    try:
        _pm_v2_write_outcome(
            closed_symbol=str(symbol),
            closed_trade_uuid=str(trade_uuid),
            closed_pnl=_safe_float(pnl),
            engine=engine,
        )
    except Exception as exc:
        try:
            print(f"[PM_V2] outcome_error err={exc!r}", flush=True)
        except Exception:
            pass


def pm_v2_record_entry_score(
    *,
    symbol:     str,
    trade_uuid: str,
    score:      float,
    conn,
) -> None:
    """
    Called when a BUY trade is approved, storing the candidate's
    score_before_filters so future PM V2 comparisons use the right scale.

    This creates/inserts into pm_v2_entry_scores. The table is created
    on first call if it does not exist.
    """
    if not settings.pm_v2_enabled:
        return
    try:
        _upsert_entry_score(
            symbol=str(symbol),
            trade_uuid=str(trade_uuid),
            score=_safe_float(score),
            conn=conn,
        )
    except Exception as exc:
        try:
            print(f"[PM_V2] entry_score_error err={exc!r}", flush=True)
        except Exception:
            pass


# ── Internal implementation ──────────────────────────────────────────────────

def _pm_v2_observe(
    *,
    incoming_symbol:  str,
    incoming_score:   float,
    incoming_rank:    int,
    rejection_reason: str,
    engine,
) -> None:
    """Core dry-run logic. Reads open positions, applies PM V2 rule, logs."""

    # Only observe capacity-type rejections
    capacity_reasons = {
        "sideways_max_open_positions",
        "max_open_positions",
        "sideways_max_exposure",
        "max_total_exposure",
        "max_symbol_exposure",
        "exposure_limit_blocked",
        "existing_position_limit_blocked",
        "caution_drawdown_position_cap",
        "caution_drawdown_exposure",
    }
    if not any(r in rejection_reason for r in capacity_reasons):
        return

    # Only observe Rank 1
    if incoming_rank != 1:
        return

    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)

    open_positions = _get_open_positions_with_scores(engine)
    if not open_positions:
        return

    # Compute age and score for each open position
    candidates_for_eviction = []
    for pos in open_positions:
        entry_time_raw = pos.get("entry_time")
        if entry_time_raw is None:
            continue
        try:
            if hasattr(entry_time_raw, "timestamp"):
                entry_ts = entry_time_raw.timestamp()
            else:
                from datetime import datetime as _dt
                entry_ts = _dt.fromisoformat(str(entry_time_raw).replace(" ", "T")).timestamp()
        except Exception:
            continue

        age_seconds = now_ts - entry_ts
        age_minutes = age_seconds / 60.0

        if age_minutes < PM_V2_MIN_AGE_MINUTES:
            continue

        entry_score = _safe_float(pos.get("entry_score") or pos.get("entry_confidence"), 0.0)
        candidates_for_eviction.append({
            "symbol":      str(pos.get("symbol", "")),
            "age_minutes": round(age_minutes, 2),
            "entry_score": round(entry_score, 6),
            "qty":         _safe_float(pos.get("quantity")),
        })

    if not candidates_for_eviction:
        # No incumbent old enough for eviction
        _append_log(_CAND_LOG, {
            "event":               "pm_v2_candidate",
            "candidate_time":      _utc(),
            "candidate_symbol":    incoming_symbol,
            "candidate_score":     round(incoming_score, 6),
            "candidate_rank":      incoming_rank,
            "reason":              rejection_reason,
            "incumbent_symbol":    None,
            "incumbent_score":     None,
            "incumbent_age":       None,
            "score_delta":         None,
            "decision":            "KEEP",
            "dry_run":             settings.pm_v2_dry_run,
            "block_reason":        "no_eligible_incumbents_old_enough",
            "open_positions_count": len(open_positions),
        })
        return

    # Pick weakest by entry_score
    weakest = min(candidates_for_eviction, key=lambda x: x["entry_score"])
    score_delta = incoming_score - weakest["entry_score"]
    would_fire  = score_delta > PM_V2_SCORE_MARGIN

    record = {
        "event":               "pm_v2_candidate",
        "candidate_time":      _utc(),
        "candidate_symbol":    incoming_symbol,
        "candidate_score":     round(incoming_score, 6),
        "candidate_rank":      incoming_rank,
        "reason":              rejection_reason,
        "incumbent_symbol":    weakest["symbol"],
        "incumbent_score":     weakest["entry_score"],
        "incumbent_age":       weakest["age_minutes"],
        "score_delta":         round(score_delta, 6),
        "decision":            "REPLACE" if would_fire else "KEEP",
        "dry_run":             settings.pm_v2_dry_run,
        "open_positions_count": len(open_positions),
        "eligible_incumbents": len(candidates_for_eviction),
    }
    _append_log(_CAND_LOG, record)

    print(
        f"[PM_V2_DRY_RUN] incoming={incoming_symbol} score={incoming_score:.5f} "
        f"evicted={weakest['symbol']} evicted_score={weakest['entry_score']:.5f} "
        f"age={weakest['age_minutes']:.1f}m delta={score_delta:.5f} "
        f"would_fire={would_fire}",
        flush=True,
    )


def _pm_v2_write_outcome(
    *,
    closed_symbol:    str,
    closed_trade_uuid: str,
    closed_pnl:       float,
    engine,
) -> None:
    """
    After any trade closes, check if there's a pending pm_v2_candidate record
    that named this symbol as 'evicted' or 'incoming'. If found, record the outcome.

    Reads the candidate log to find matching pending records.
    Writes to pm_v2_outcomes.jsonl.
    """
    if not _CAND_LOG.exists():
        return

    matching_candidates = []
    try:
        with _LOG_LOCK:
            with _CAND_LOG.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("event") != "pm_v2_candidate":
                            continue
                        if rec.get("outcome_recorded"):
                            continue
                        # Match on incumbent_symbol (the position that was retained)
                        if rec.get("incumbent_symbol") == closed_symbol:
                            matching_candidates.append(rec)
                    except Exception:
                        pass
    except Exception:
        return

    if not matching_candidates:
        return

    # Take the most recent unmatched candidate for this symbol
    matched = matching_candidates[-1]

    outcome = {
        "event":                 "pm_v2_outcome",
        "timestamp":             _utc(),
        "candidate_time":        matched.get("candidate_time"),
        "candidate_symbol":      matched.get("candidate_symbol"),
        "candidate_score":       matched.get("candidate_score"),
        "incumbent_symbol":      closed_symbol,
        "incumbent_score":       matched.get("incumbent_score"),
        "incumbent_age_at_decision": matched.get("incumbent_age"),
        "score_delta":           matched.get("score_delta"),
        "decision":              matched.get("decision"),

        # Outcome: retained incumbent closed for this PnL
        "incumbent_realized_pnl":  closed_pnl,
        "trade_uuid":              closed_trade_uuid,

        # Outcome: incoming candidate PnL -- we can't know this for the candidate
        # because it was never executed. We write None and let the post-analysis fill it in
        # if the same symbol traded shortly after.
        "candidate_realized_pnl":  None,
        "net_delta":               None,
        "note": "candidate_pnl not available (trade was never taken)",
    }

    _append_log(_OUT_LOG, outcome)
    print(
        f"[PM_V2_OUTCOME] evicted={closed_symbol} pnl={closed_pnl:.6f} "
        f"incoming_would_have_been={matched.get('incoming_symbol')} "
        f"would_fire={matched.get('replacement_would_fire')}",
        flush=True,
    )


def _upsert_entry_score(
    *,
    symbol:     str,
    trade_uuid: str,
    score:      float,
    conn,
) -> None:
    from sqlalchemy import text as _text
    # Create table if not exists
    conn.execute(_text("""
        CREATE TABLE IF NOT EXISTS pm_v2_entry_scores (
            trade_uuid TEXT PRIMARY KEY,
            symbol     TEXT NOT NULL,
            score      REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """))
    conn.execute(_text("""
        INSERT INTO pm_v2_entry_scores (trade_uuid, symbol, score, created_at)
        VALUES (:trade_uuid, :symbol, :score, :created_at)
        ON CONFLICT (trade_uuid) DO UPDATE SET score = EXCLUDED.score
    """), {
        "trade_uuid": trade_uuid,
        "symbol":     symbol,
        "score":      score,
        "created_at": _utc(),
    })
