# Quant Fund OS Consolidation Plan

## Goal

Replace legacy `main.py` responsibilities gradually with tested modules while
preserving the proven PostgreSQL-only, audit-safe runtime behavior.

## Replacement order

1. Execution telemetry truth
2. Control-plane adapter
3. Market-data adapter
4. Feature-cycle adapter
5. Portfolio/accounting adapter
6. Atomic execution adapter
7. API status projection
8. Thin legacy launcher

## Non-negotiable acceptance gates

- PostgreSQL remains the accounting, trade, and position authority.
- SQLite must not become a runtime portfolio or execution authority.
- Audit mode must persist zero fills.
- Rejected fills must never count as final applied fills.
- `/resume` must reach market, feature, quality, and execution cycles.
- `/pause` must be observed by the active loop.
- Status must reconcile cash + exposure = equity within rounding tolerance.
- No full replacement is promoted until protected audit evidence passes.

## Current first module

`qfos_runtime.execution_telemetry` defines truthful persisted-versus-rejected
execution counters. It is intentionally not wired into `main.py` yet.
