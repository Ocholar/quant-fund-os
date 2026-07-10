import os
import json
import uuid
import datetime
import logging
import subprocess
from enum import Enum
from pathlib import Path
import yaml

logger = logging.getLogger("observability")

# ---------------------------------------------------------------------------
# Event Types
# ---------------------------------------------------------------------------

class EventType(Enum):
    CANDIDATE_RANKED          = "candidate_ranked"
    CANDIDATE_FILTERED        = "candidate_filtered"
    CANDIDATE_APPROVED        = "candidate_approved"
    CANDIDATE_TERMINAL        = "candidate_terminal"
    BATCH_FILTERED            = "batch_filtered"       # Gate A / B: whole-list vetoes
    TRADE_EXECUTION_STARTED   = "trade_execution_started"
    TRADE_EXECUTION_FAILED    = "trade_execution_failed"
    TRADE_PERSISTED           = "trade_persisted"
    TRADE_OPENED              = "trade_opened"
    TRADE_EXECUTED            = "trade_executed"       # Legacy
    TRADE_OPEN                = "trade_open"           # Legacy
    TRADE_EXITED              = "trade_exited"
    CYCLE_SUMMARY             = "cycle_summary"

# ---------------------------------------------------------------------------
# Rejection / Filter Enums
# ---------------------------------------------------------------------------

class RejectionReason(Enum):
    """
    Controlled vocabulary for per-candidate rejection reasons.
    reason_version = 1
    """
    SIGNAL_TOO_WEAK          = "SIGNAL_TOO_WEAK"
    QUARANTINE               = "QUARANTINE"
    MAX_EXPOSURE             = "MAX_EXPOSURE"
    MAX_POSITIONS            = "MAX_POSITIONS"
    POSITION_ALREADY_OPEN    = "POSITION_ALREADY_OPEN"
    ACTIVE_ORDER             = "ACTIVE_ORDER"
    TREND_MISMATCH           = "TREND_MISMATCH"
    VOLATILITY_TOO_HIGH      = "VOLATILITY_TOO_HIGH"
    LIQUIDITY_TOO_LOW        = "LIQUIDITY_TOO_LOW"
    FEATURE_NOT_READY        = "FEATURE_NOT_READY"
    QUOTE_FILTER             = "QUOTE_FILTER"
    COOLDOWN                 = "COOLDOWN"
    CASH_INSUFFICIENT        = "CASH_INSUFFICIENT"
    MARGIN_INSUFFICIENT      = "MARGIN_INSUFFICIENT"
    RISK_MANAGER_REJECTED    = "RISK_MANAGER_REJECTED"
    SYMBOL_MUTEX             = "SYMBOL_MUTEX"
    ORDER_VALIDATION_FAILED  = "ORDER_VALIDATION_FAILED"
    EXECUTION_FAILED         = "EXECUTION_FAILED"
    ATOMIC_PERSISTENCE_FAILED = "ATOMIC_PERSISTENCE_FAILED"
    OTHER                    = "OTHER"

REJECTION_REASON_VERSION = 1


class RiskRule(Enum):
    """
    Structured sub-reason for RISK_MANAGER_REJECTED events.
    Stored inside details.risk_rule.
    """
    MAX_DAILY_LOSS          = "MAX_DAILY_LOSS"
    BLOCKED_DRAWDOWN        = "BLOCKED_DRAWDOWN"
    NEAR_BLOCKED_DRAWDOWN   = "NEAR_BLOCKED_DRAWDOWN"
    STRATEGY_BLOCKED        = "STRATEGY_BLOCKED"
    RISK_OFF_REGIME         = "RISK_OFF_REGIME"
    BUYS_DISABLED           = "BUYS_DISABLED"
    SYSTEM_PAUSED           = "SYSTEM_PAUSED"
    CAUTION_POSITION_CAP    = "CAUTION_POSITION_CAP"
    CAUTION_EXPOSURE_CAP    = "CAUTION_EXPOSURE_CAP"
    SYMBOL_BAD_HISTORY      = "SYMBOL_BAD_HISTORY"
    DAILY_LOSS_LIMIT        = "DAILY_LOSS_LIMIT"
    UNSPECIFIED             = "UNSPECIFIED"


class FilterStage(Enum):
    """
    Reserved stage labels for the full pipeline lifecycle.
    filter_stage_version = 1

    Stage | Label         | Phase
    ------|---------------|----------
    1     | ENTRY_QUALITY | 2B (done)
    2     | ALLOCATOR     | 2C
    3     | EXECUTION     | 2D
    4     | POSITION_MGMT | future
    5     | EXIT          | future
    """
    ENTRY_QUALITY = "ENTRY_QUALITY"
    ALLOCATOR     = "ALLOCATOR"
    EXECUTION     = "EXECUTION"
    POSITION_MGMT = "POSITION_MGMT"
    EXIT          = "EXIT"

    @classmethod
    def from_int(cls, stage_int: int) -> "FilterStage":
        mapping = {
            1: cls.ENTRY_QUALITY,
            2: cls.ALLOCATOR,
            3: cls.EXECUTION,
            4: cls.POSITION_MGMT,
            5: cls.EXIT,
        }
        return mapping.get(stage_int, cls.ENTRY_QUALITY)

FILTER_STAGE_VERSION = 1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rank_percentile(rank, ranking_population) -> float:
    """
    1.0 = best rank (rank 1), 0.0 = worst rank (rank N).
    Safe against None and population of 1.
    """
    try:
        r = int(rank)
        n = int(ranking_population)
        if n <= 1:
            return 1.0
        return round((n - r) / (n - 1), 6)
    except Exception:
        return None


def _make_allocator_state(
    cash=None,
    equity=None,
    exposure=None,
    available_cash=None,
    reserved_cash=None,
    open_positions=None,
    position_count=None,
    pending_orders=None,
    buy_slots_remaining=None,
    risk_mode=None,
    paper_balance_version=None,
    drawdown=None,
) -> dict:
    """
    Build a stable allocator_state snapshot dict.
    All fields are optional; missing values stay None rather than being omitted,
    so the schema shape is constant for all events.
    """
    pos = position_count if position_count is not None else open_positions
    return {
        "cash": cash,
        "equity": equity,
        "exposure": exposure,
        "available_cash": available_cash,
        "reserved_cash": reserved_cash,
        "open_positions": open_positions,
        "position_count": pos,
        "pending_orders": pending_orders,
        "buy_slots_remaining": buy_slots_remaining,
        "risk_mode": risk_mode,
        "paper_balance_version": paper_balance_version if paper_balance_version is not None else 1,
        "drawdown": drawdown,
    }

# ---------------------------------------------------------------------------
# Observability Manager
# ---------------------------------------------------------------------------

class ObservabilityManager:
    def __init__(self, config_path="config/observability.yaml"):
        self.config_path = config_path
        self.config = self._load_config()
        self.schema_version = self.config.get("schema_version", "1.0")
        self.log_root = Path(self.config.get("log_root", "logs"))
        self.candidates_dir = Path(self.config.get("candidates_dir", "logs/candidates"))
        self.trades_dir = Path(self.config.get("trades_dir", "logs/trades"))
        self.fail_safe = self.config.get("fail_safe", True)

        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        self.trades_dir.mkdir(parents=True, exist_ok=True)

        self.metadata = self._capture_metadata()
        self._handles = {}
        self._cycle_candidate_ids = {}  # (cycle_id, symbol) -> candidate_id
        self._candidate_terminal = set()
        
        self.write_run_manifest()

    # --- Candidate Registry -------------------------------------------------

    def register_candidate(self, cycle_id: int, symbol: str, candidate_id: str, rank: int = None, ranking_population: int = None):
        """Register a candidate_id for lookup by (cycle_id, symbol)."""
        self._cycle_candidate_ids[(cycle_id, symbol)] = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "rank": rank,
            "ranking_population": ranking_population
        }

    def get_candidate_id(self, cycle_id: int, symbol: str) -> str:
        """Retrieve the registered candidate_id for (cycle_id, symbol), or None."""
        val = self._cycle_candidate_ids.get((cycle_id, symbol))
        return val["candidate_id"] if val else None

    def get_candidate_info(self, cycle_id: int, symbol: str) -> dict:
        """Retrieve the registered candidate info for (cycle_id, symbol), or None."""
        return self._cycle_candidate_ids.get((cycle_id, symbol))

    def register_trade_id(self, cycle_id: int, symbol: str, trade_id: str):
        """Register a trade_id associated with an approved candidate."""
        if (cycle_id, symbol) in self._cycle_candidate_ids:
            self._cycle_candidate_ids[(cycle_id, symbol)]["trade_id"] = trade_id

    def get_trade_id(self, cycle_id: int, symbol: str) -> str:
        """Retrieve the registered trade_id for (cycle_id, symbol), or None."""
        val = self._cycle_candidate_ids.get((cycle_id, symbol))
        return val.get("trade_id") if val else None

    def clear_cycle(self, cycle_id: int):
        """Remove all registry entries for the given cycle_id to prevent stale lookups."""
        stale_keys = [k for k in list(self._cycle_candidate_ids.keys()) if k[0] == cycle_id]
        for k in stale_keys:
            del self._cycle_candidate_ids[k]

    def mark_candidate_terminal(self, candidate_id: str):
        if candidate_id:
            self._candidate_terminal.add(candidate_id)

    def unresolved_candidates(self, cycle_id: int):
        return [
            info for (registered_cycle, _), info in self._cycle_candidate_ids.items()
            if registered_cycle == cycle_id and info.get("candidate_id") not in self._candidate_terminal
        ]

    def write_run_manifest(self, trade_interval_seconds=10, symbols=57, market="MEXC paper"):
        """Save a single JSON manifest for the entire run, if not already present."""
        manifest_path = self.log_root / "run_manifest.json"
        if not manifest_path.exists():
            manifest = {
                "paper_run_id": self.metadata.get("paper_run_id", "unknown"),
                "git_commit": self.metadata.get("git_commit", "unknown"),
                "config_hash": self.metadata.get("config_hash", "unknown"),
                "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                "trade_interval_seconds": trade_interval_seconds,
                "symbols": symbols,
                "market": market
            }
            try:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to write run manifest: {e}")

    # --- Internal -----------------------------------------------------------

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to load observability config: {e}")
        return {}

    def _capture_metadata(self):
        git_commit = None
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            ).decode("utf-8").strip()
        except Exception:
            git_commit = os.environ.get("GIT_COMMIT", "unknown")
        return {
            "git_commit": git_commit,
            "config_hash": os.environ.get("CONFIG_HASH", None),
            "paper_run_id": os.environ.get("PAPER_RUN_ID", None),
            "model_version": os.environ.get("MODEL_VERSION", "1.0.0"),
        }

    def _get_file_handle(self, target_dir, prefix):
        date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        file_path = target_dir / f"{prefix}_{date_str}.jsonl"
        key = str(file_path)
        if key not in self._handles or self._handles[key].closed:
            self._handles[key] = open(file_path, "a", encoding="utf-8")
        return self._handles[key]

    def emit(self, event_type: EventType, payload: dict):
        try:
            target_dir = (
                self.trades_dir
                if event_type in (
                    EventType.TRADE_EXECUTION_STARTED,
                    EventType.TRADE_EXECUTION_FAILED,
                    EventType.TRADE_PERSISTED,
                    EventType.TRADE_OPENED,
                    EventType.TRADE_EXECUTED,
                    EventType.TRADE_OPEN,
                    EventType.TRADE_EXITED
                )
                else self.candidates_dir
            )
            prefix = "trades" if target_dir == self.trades_dir else "candidates"
            record = {
                "schema_version": self.schema_version,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "event_type": event_type.value,
                "metadata": self.metadata,
                "payload": payload,
            }
            handle = self._get_file_handle(target_dir, prefix)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
        except Exception as e:
            if self.fail_safe:
                logger.warning(f"[Observability Fail-Safe] Error emitting {event_type}: {e}")
            else:
                raise

    def close(self):
        for handle in self._handles.values():
            if not handle.closed:
                handle.close()


_manager = ObservabilityManager()

# ---------------------------------------------------------------------------
# Event Emitter
# ---------------------------------------------------------------------------

class EventEmitter:
    @staticmethod
    def generate_uuid():
        return str(uuid.uuid4())

    # --- Candidate Ranked ---------------------------------------------------

    def candidate_ranked(
        self,
        cycle_id: int,
        rank,
        symbol: str,
        strength: float,
        momentum: float,
        volatility: float,
        confidence: float,
        regime: str,
        source: str,
        score_before_filters,
        score_after_filters: float = None,
        ranking_population: int = None,
        candidate_id: str = None,
        decision: str = None,
        filter_reason: str = None,
    ) -> str:
        cid = candidate_id or self.generate_uuid()
        rp = _rank_percentile(rank, ranking_population) if rank is not None else None
        payload = {
            "cycle_id": cycle_id,
            "candidate_id": cid,
            "rank": rank,
            "rank_percentile": rp,
            "ranking_population": ranking_population,
            "symbol": symbol,
            "strength": strength,
            "momentum": momentum,
            "volatility": volatility,
            "confidence": confidence,
            "regime": regime,
            "source": source,
            "score_before_filters": score_before_filters,
            "score_after_filters": score_after_filters if score_after_filters is not None else score_before_filters,
            "decision": decision,
            "filter_reason": filter_reason,
        }
        _manager.emit(EventType.CANDIDATE_RANKED, payload)
        return cid

    # --- Candidate Filtered -------------------------------------------------

    def candidate_filtered(
        self,
        candidate_id: str,
        cycle_id: int,
        symbol: str,
        rank,
        reason: RejectionReason,
        filter_name: str,
        filter_stage: int = None,
        details: dict = None,
        raw_reason: str = None,
        ranking_population: int = None,
        allocator_state: dict = None,
        selection_terminal: bool = True,
        parent_cycle: int = None,
        candidate_generation: int = 0,
        retry_count: int = 0,
        decision_latency_ms: float = None,
        stage_latency_ms: float = None,
        pipeline_latency_ms: float = None,
    ):
        terminal_filter = FilterStage.from_int(filter_stage).value if filter_stage is not None else None
        rp = _rank_percentile(rank, ranking_population) if rank is not None and ranking_population is not None else None
        payload = {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "rank": rank,
            "rank_percentile": rp,
            "ranking_population": ranking_population,
            "reason": reason.value if isinstance(reason, RejectionReason) else str(reason),
            "reason_version": REJECTION_REASON_VERSION,
            "raw_reason": raw_reason,
            "filter_name": filter_name,
            "filter_stage": filter_stage,
            "filter_stage_version": FILTER_STAGE_VERSION,
            "terminal_filter": terminal_filter,
            "selection_terminal": selection_terminal,
            "details": details or {},
            "allocator_state": allocator_state,
            "parent_cycle": parent_cycle,
            "candidate_generation": candidate_generation,
            "retry_count": retry_count,
            "decision_latency_ms": decision_latency_ms,
            "stage_latency_ms": stage_latency_ms,
            "pipeline_latency_ms": pipeline_latency_ms,
        }
        _manager.emit(EventType.CANDIDATE_FILTERED, payload)
        _manager.mark_candidate_terminal(candidate_id)

    def candidate_terminal(self, candidate_id: str, cycle_id: int, symbol: str, state: str, reason: str = None):
        """Record the single terminal outcome for a ranked candidate."""
        if candidate_id in _manager._candidate_terminal:
            return False
        _manager.emit(EventType.CANDIDATE_TERMINAL, {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "state": state,
            "reason": reason,
        })
        _manager.mark_candidate_terminal(candidate_id)
        return True

    def expire_unresolved_candidates(self, cycle_id: int, reason: str = "not_submitted_to_execution"):
        expired = 0
        for info in _manager.unresolved_candidates(cycle_id):
            if self.candidate_terminal(
                info.get("candidate_id"), cycle_id, info.get("symbol", "UNKNOWN"), "EXPIRED", reason
            ):
                expired += 1
        return expired

    # --- Batch Filtered (Gates A, B) ----------------------------------------

    def batch_filtered(
        self,
        cycle_id: int,
        filter_stage: int,
        filter_name: str,
        reason: RejectionReason,
        affected_candidates: list,
        raw_reason: str = None,
        details: dict = None,
        allocator_state: dict = None,
    ):
        """
        Emit one event for a batch veto that affects multiple candidates at once.
        Use when a gate drops the entire buy list (e.g. paused, hard exposure cap).
        """
        terminal_filter = FilterStage.from_int(filter_stage).value if filter_stage is not None else None
        payload = {
            "cycle_id": cycle_id,
            "filter_stage": filter_stage,
            "filter_stage_version": FILTER_STAGE_VERSION,
            "terminal_filter": terminal_filter,
            "filter_name": filter_name,
            "reason": reason.value if isinstance(reason, RejectionReason) else str(reason),
            "reason_version": REJECTION_REASON_VERSION,
            "raw_reason": raw_reason,
            "affected_count": len(affected_candidates),
            "affected_candidates": list(affected_candidates),
            "details": details or {},
            "allocator_state": allocator_state,
        }
        _manager.emit(EventType.BATCH_FILTERED, payload)

    # --- Candidate Approved -------------------------------------------------

    def candidate_approved(
        self,
        candidate_id: str,
        cycle_id: int,
        symbol: str,
        rank,
        ranking_population: int = None,
        allocator_state: dict = None,
        parent_cycle: int = None,
        candidate_generation: int = 0,
        retry_count: int = 0,
        stage_latency_ms: float = None,
        pipeline_latency_ms: float = None,
    ):
        rp = _rank_percentile(rank, ranking_population) if rank is not None and ranking_population is not None else None
        payload = {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "rank": rank,
            "rank_percentile": rp,
            "ranking_population": ranking_population,
            "selection_terminal": True,
            "allocator_state": allocator_state,
            "parent_cycle": parent_cycle,
            "candidate_generation": candidate_generation,
            "retry_count": retry_count,
            "stage_latency_ms": stage_latency_ms,
            "pipeline_latency_ms": pipeline_latency_ms,
        }
        _manager.emit(EventType.CANDIDATE_APPROVED, payload)

    # --- Trade Events -------------------------------------------------------

    def trade_execution_started(
        self,
        candidate_id: str,
        cycle_id: int,
        symbol: str,
        allocator_state: dict = None,
        trade_id: str = None,
    ) -> str:
        tid = trade_id or self.generate_uuid()
        payload = {
            "trade_id": tid,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "allocator_state": allocator_state,
        }
        _manager.emit(EventType.TRADE_EXECUTION_STARTED, payload)
        return tid

    def trade_execution_failed(
        self,
        candidate_id: str,
        trade_id: str,
        cycle_id: int,
        symbol: str,
        gate: str,
        reason: RejectionReason,
        raw_reason: str = None,
        details: dict = None,
    ):
        payload = {
            "trade_id": trade_id,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "gate": gate,
            "reason": reason.value if isinstance(reason, RejectionReason) else str(reason),
            "raw_reason": raw_reason,
            "details": details or {},
        }
        _manager.emit(EventType.TRADE_EXECUTION_FAILED, payload)
        self.candidate_terminal(candidate_id, cycle_id, symbol, "REJECTED", raw_reason or str(reason))

    def trade_persisted(
        self,
        candidate_id: str,
        trade_id: str,
        cycle_id: int,
        symbol: str,
        quantity: float,
        fill_price: float,
    ):
        payload = {
            "trade_id": trade_id,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "quantity": quantity,
            "fill_price": fill_price,
        }
        _manager.emit(EventType.TRADE_PERSISTED, payload)

    def trade_opened(
        self,
        candidate_id: str,
        trade_id: str,
        cycle_id: int,
        symbol: str,
    ):
        payload = {
            "trade_id": trade_id,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
        }
        _manager.emit(EventType.TRADE_OPENED, payload)
        self.candidate_terminal(candidate_id, cycle_id, symbol, "EXECUTED")

    def trade_executed(
        self,
        candidate_id: str,
        cycle_id: int,
        symbol: str,
        entry_price: float,
        position_size: float,
        cash_available: float,
        current_exposure: float,
        open_positions: int,
        trade_id: str = None,
    ) -> str:
        tid = trade_id or self.generate_uuid()
        payload = {
            "trade_id": tid,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "entry_price": entry_price,
            "position_size": position_size,
            "cash_available": cash_available,
            "current_exposure": current_exposure,
            "open_positions": open_positions,
        }
        _manager.emit(EventType.TRADE_EXECUTED, payload)
        return tid

    def trade_open(self, trade_id: str, candidate_id: str, symbol: str):
        payload = {
            "trade_id": trade_id,
            "candidate_id": candidate_id,
            "symbol": symbol,
        }
        _manager.emit(EventType.TRADE_OPEN, payload)

    def trade_exited(
        self,
        trade_id: str,
        candidate_id: str,
        symbol: str,
        exit_price: float,
        holding_time_seconds: float,
        realized_pnl: float,
        exit_reason: str,
        strategy: str = None,
        fees_paid: float = 0.0,
        R_multiple: float = None,
        MFE: float = None,
        MAE: float = None,
    ):
        payload = {
            "trade_id": trade_id,
            "candidate_id": candidate_id,
            "symbol": symbol,
            "exit_price": exit_price,
            "holding_time_seconds": holding_time_seconds,
            "realized_pnl": realized_pnl,
            "exit_reason": exit_reason,
            "strategy": strategy,
            "fees_paid": fees_paid,
            "R_multiple": R_multiple,
            "MFE": MFE,
            "MAE": MAE,
        }
        _manager.emit(EventType.TRADE_EXITED, payload)

    # --- Cycle Summary ------------------------------------------------------

    def cycle_summary(
        self,
        cycle_id: int,
        total_candidates: int,
        candidates_above_threshold: int,
        filtered_count: int,
        approved_count: int,
        executed_count: int,
        regime: str,
        evaluation_time_ms: float,
        ranking_time_ms: float = None,
        filter_time_ms: float = None,
        execution_time_ms: float = None,
    ):
        payload = {
            "cycle_id": cycle_id,
            "total_candidates": total_candidates,
            "candidates_above_threshold": candidates_above_threshold,
            "filtered_count": filtered_count,
            "approved_count": approved_count,
            "executed_count": executed_count,
            "regime": regime,
            "evaluation_time_ms": evaluation_time_ms,
            "ranking_time_ms": ranking_time_ms,
            "filter_time_ms": filter_time_ms,
            "execution_time_ms": execution_time_ms,
        }
        _manager.emit(EventType.CYCLE_SUMMARY, payload)


events = EventEmitter()
