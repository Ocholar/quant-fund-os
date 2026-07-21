# Quant Fund OS

## 1. Project Mission
Quant Fund OS is an autonomous AI-driven MEXC Spot paper-trading system whose objective is to establish positive expectancy and robust risk-adjusted profitability before any controlled live deployment.

## 2. Current Status
- **Paper trading only**
- **Live trading disabled**
- **PM V2 disabled**
- **Current signal thresholds**:
  - `ENTRY_MIN_SIGNAL_SIDEWAYS=0.0017`
  - `ENTRY_MIN_SIGNAL_TRENDING=0.0015`
- **Database**: PostgreSQL is the canonical runtime database. SQLite files are legacy/non-authoritative.
- **Infrastructure**: Docker Desktop on the previous 8 GB host was not sufficiently reliable for the required soak. The project is paused pending migration to a machine with approximately 32 GB RAM.
- **Previous Validation**: The previous Phase IVB soak failed to complete the required 6 hours because Docker/WSL infrastructure failed after approximately 3h21m. The application-level failure was not conclusively identified as the cause of termination. A 10-minute post-fix validation showed no observed `QueryCanceled`, `TRADE_BOUNDARY_REJECT`, `FEATURE_HANDOFF_ERROR`, or `OBSERVABILITY_ERROR`.
- **Readiness**: The full 6-hour durability gate remains **unpassed**. The system is **not ready for live trading**.

## 3. Architecture
```text
Market Data
    ↓
Feature Store
    ↓
Regime Detection
    ↓
AI / Evolutionary Signal Generation
    ↓
Candidate Ranking
    ↓
Signal Quality Gate
    ↓
Risk / Allocation
    ↓
Paper Execution
    ↓
PostgreSQL Ledger
    ↓
Monitoring / Dashboard / Telegram
```
- **Market Data / Feature Store**: `data/` and related services.
- **AI / Signal Generation**: `ai/` module.
- **Candidate Ranking / Gate / Risk**: `core/` modules (allocators, gates).
- **Execution**: `services/` interacting with MEXC.
- **Ledger**: PostgreSQL interacting via `core/` and `data/` models.

## 4. Database
- **Canonical Database**: PostgreSQL
- **Database Name**: Configured via `.env` (default typically `qfos` or similar).
- **Initialization & Schema**: Schema definitions and migrations are handled via the authoritative SQLAlchemy models/bootstrap SQL found in the repository.
- **Important Tables**: Contains tables for features, candidates, orders, ledger, and accounting relationships. No raw passwords or secrets are stored in the codebase schema files.

## 5. Configuration
- **Environment**: Use `.env.example` as the template for `.env`.
- **Required Variables**: API keys (safe/paper), PostgreSQL DSN, Telegram tokens, and environment toggles.
- **Toggles**: Paper mode must be ON. PM V2 status is disabled by default until proven. Signal thresholds and risk limits are configured in `core/config.py` and via environment variables.

## 6. Development Startup
1. Clone the repository.
2. Create `.env` from `.env.example`.
3. Populate credentials safely (do not commit them).
4. Start PostgreSQL, Redis, and the application (using `docker-compose.yml` or local startup scripts).
5. Verify system health.
6. Verify paper mode is strictly enabled.

## 7. Testing
Authoritative test commands are defined via `pytest`. Run tests (e.g., `pytest tests/`) to ensure the core logic and integrations are functional before attempting validation.

## 8. Validation Protocol
No stage may be skipped:
1. 10–15 minute smoke test
        ↓
2. 6-hour soak
        ↓
3. 24-hour validation
        ↓
4. multi-day profitability validation
        ↓
5. controlled live canary

## 9. Current Blockers
Infrastructure capacity is the primary blocker. The next machine should ideally have:
- **RAM**: 32 GB
- **CPU**: 4+ cores
- **Disk**: ample free SSD capacity
- **OS**: Linux preferred

A 32 GB Linux host is the preferred next validation environment.
