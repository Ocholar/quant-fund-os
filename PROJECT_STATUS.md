# PROJECT_STATUS

## Mission
The single objective of Quant Fund OS is to build a profitable autonomous AI paper-trading system for MEXC Spot before any controlled live trading.

## Current State
- The repository is clean, portable, and reproducible.
- Paper trading is fully operational (live trading is strictly disabled).
- The feature store and AI generation pipelines exist and function.
- Configuration and threshold toggles are available via `.env` and `core/config.py`.

## What is not proven
- The 6-hour durability and runtime stability gate has NOT been passed.
- Robust risk-adjusted profitability is NOT proven over extended horizons.
- The system's ability to maintain state and recover from extended disconnection cleanly over multiple days is unverified.

## Important Historical Findings
- **Database**: PostgreSQL is the canonical runtime database. SQLite is deprecated for runtime usage.
- **Infrastructure Failure**: Docker/WSL on an 8 GB host failed after ~3h21m during a previous Phase IVB soak. A 32 GB host is required to continue.
- **PM V2**: PM V2 is disabled by default due to outstanding validation requirements.
- **Current Thresholds**: 
  - `ENTRY_MIN_SIGNAL_SIDEWAYS=0.0017`
  - `ENTRY_MIN_SIGNAL_TRENDING=0.0015`
- **Signal & Concentration**: Past findings indicated potential symbol concentration and confidence calibration needs, which informed current threshold and allocator logic.

## Exact Next Steps
The next agent should execute:
1. Obtain 32 GB machine
2. Clone clean repository
3. Configure .env safely
4. Start PostgreSQL/Redis/application
5. Verify runtime health
6. Run 10–15 minute smoke test
7. Run full 6-hour soak
8. Collect sanitized evidence
9. Analyze results
10. Continue profitability engineering only after infrastructure durability is proven

## Absolute Prohibitions
The next agent **must not**:
- enable live trading
- enable PM V2 prematurely
- treat the previous partial soak as a successful 6-hour soak
- assume profitability has been proven
- reintroduce large runtime logs into Git
- commit secrets
- make repeated speculative patches without evidence
- modify unrelated subsystems during validation
