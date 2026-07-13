# Research Auditor — Architecture & Reproducibility Document

## Overview

The Research Run Auditor is a strictly **read-only**, **offline**, **deterministic** Python script that
consumes JSONL telemetry produced by the Quant Fund OS trading engine and generates human-readable
markdown research reports and machine-readable JSON metrics files.

It has no connection to the live trading engine, no database access, and cannot influence execution.

---

## Inputs

| Source | Pattern | Contents |
|--------|---------|----------|
| Candidate logs | `logs/candidates/candidates_YYYY-MM-DD.jsonl` | `candidate_ranked`, `candidate_filtered`, `candidate_approved`, `candidate_terminal`, `cycle_summary` events |
| Trade logs | `logs/trades/trades_YYYY-MM-DD.jsonl` | `trade_executed`, `trade_open`, `trade_exited` events |

All inputs are append-only JSONL files. Each line is an independent JSON object with the schema:
```json
{
  "schema_version": "1.0",
  "timestamp": "ISO-8601",
  "event_type": "...",
  "metadata": { "git_commit": "...", ... },
  "payload": { ... }
}
```

---

## Outputs

| File | Description |
|------|-------------|
| `research/daily_research_report_YYYY-MM-DD.md` | Human-readable 11-section research report |
| `research/daily_research_metrics_YYYY-MM-DD.json` | Machine-readable metrics (all computed values) |

---

## Architecture

### Phase 1 — Parsing

`iter_jsonl(path)` streams each JSONL file line-by-line to avoid loading multi-hundred-MiB files into memory at once. Malformed JSON lines are counted, reported, and skipped — they never raise exceptions.

### Phase 2 — In-Memory Records

Two primary in-memory data structures are built:

- `Dict[candidate_id, CandidateRecord]` — one record per unique `candidate_id` UUID. Populated from `candidate_ranked`, `candidate_filtered`, `candidate_approved`, and `candidate_terminal` events.
- `Dict[trade_id, TradeRecord]` — one record per unique `trade_id` UUID. Populated from `trade_executed`, `trade_open`, and `trade_exited` events.

### Phase 3 — UUID Linkage

`reconcile()` joins `TradeRecord` to `CandidateRecord` via `candidate_id`. This is a one-to-one join enforced by the observability schema. Orphans are counted and reported, never silently dropped.

### Phase 4 — Analytics

All analytics are pure functions over the in-memory records. No state is mutated. No external calls are made.

| Section | Function |
|---------|----------|
| Runtime Summary | `compute_runtime_summary` |
| Candidate Funnel | `compute_candidate_funnel` |
| Reject Analysis | `compute_reject_analysis` |
| Trading Performance | `compute_trading_performance` |
| Signal Analysis | `compute_signal_analysis` |
| Ranking Analysis | `compute_ranking_analysis` |
| Regime Analysis | `compute_regime_analysis` |
| Feature Correlation | `compute_feature_correlation` |
| Filter Effectiveness | `compute_filter_effectiveness` |
| Top Findings | `compute_top_findings` |

### Phase 5 — Report Generation

`generate_markdown_report()` and `generate_metrics_json()` are pure functions that accept pre-computed analytics dicts and return strings/dicts. No I/O occurs in these functions.

### Phase 6 — Determinism Check

After writing the reports, the metrics dict is regenerated from the same in-memory objects and compared (excluding `generated_at` wall-clock timestamp). A PASS confirms the auditor is deterministic for a given input.

---

## Usage

```bash
# Process today's logs
python research_auditor.py

# Process a specific date
python research_auditor.py --date 2026-07-10

# Process all available dates
python research_auditor.py --all
```

---

## Assumptions

1. JSONL files are append-only; the auditor does not handle concurrent writes.
2. UUID uniqueness is maintained by the engine's `generate_uuid()` function.
3. The `candidate_id` field is the primary join key between candidates and trades.
4. Feature snapshots (`features` field in `candidate_ranked`) are only present in events after the Phase 3B observability patch (after 2026-07-13). Pre-patch events will have `features: null`.
5. Holding times are in seconds (as emitted by the engine).
6. PnL values are in the quote currency (USDT).

---

## Limitations

1. **Feature correlation requires ≥3 completed trades** with feature snapshots. Pre-patch logs have no feature data.
2. **Sharpe ratio requires ≥5 completed trades**. Under this threshold, it is reported as "Insufficient data."
3. **Filter effectiveness is a same-symbol proxy**, not a counterfactual simulation. It answers "did this symbol later trade profitably?" not "would this candidate have been profitable?"
4. **Regime analysis** requires trades with both a linked candidate (for regime) and an exit PnL. Sparse trade logs will produce empty regime tables.
5. **Stall detection** uses cycle ID gaps > 5 as a heuristic. This may produce false positives if the cycle ID space is intentionally sparse.

---

## Reproducibility

Given the same input JSONL files, the auditor will produce bit-identical output (excluding the `generated_at` timestamp field). To reproduce a report:

1. Ensure the same `logs/candidates/candidates_YYYY-MM-DD.jsonl` and `logs/trades/trades_YYYY-MM-DD.jsonl` files are present.
2. Run: `python research_auditor.py --date YYYY-MM-DD`
3. The output files will be written to `research/`.

No database, no network calls, no randomness.

---

## Safety Guarantees

The auditor:
- Opens log files in **read mode only** (`open(..., 'r')`)
- Never writes to `logs/`, `main.py`, `observability.py`, or any database
- Never imports from `main.py` or the trading engine
- Never calls the allocator or execution engine
- Has no side effects beyond writing to `research/`
