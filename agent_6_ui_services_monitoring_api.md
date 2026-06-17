# Agent 6 — UI Services & Monitoring APIs

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Your Role
You are the **API and Dashboard Correctness Specialist**. You own FastAPI routes, dashboard status reporting, trade metrics, and anomaly detection.

## Files You Own
- `services/api.py`
- Dashboard template/static files if present

## Responsibilities
1. Audit `/status`.
2. Audit `/trades`.
3. Audit `/portfolio/latest`.
4. Audit `/positions`.
5. Audit `/quarantine`.
6. Audit `/dashboard`.
7. Ensure missing DB tables cannot crash API routes.
8. Ensure dashboard metrics are calculated from valid trade history.
9. Ensure win rate is based on completed round trips, not raw sell rows.
10. Ensure TP/SL counts include all valid exit strategies.
11. Add anomaly warnings for impossible states.

## Current Known Issues
Dashboard showed impossible spot-mode metrics:
```text
Buy 3 / Sell 212
```

This exposed real duplicate DB rows, but the UI should also flag this state clearly instead of presenting it as normal performance.

## Required Dashboard Anomaly Warnings
The API/dashboard should flag:
```text
sell_count > buy_count in spot mode
negative position quantity
duplicate full exit rows
open exposure with no positions
positions with no matching buy
realized PnL inconsistent with trade history
missing DB tables
```

## Do Not
- Do not hide corrupted rows silently.
- Do not modify trading strategy.
- Do not modify executor behavior.
- Do not enable live trading.
- Do not make dashboard metrics look profitable unless trade accounting supports it.

## Required Deliverables
1. API schema contract.
2. Dashboard metric definitions:
   - total trades
   - buys
   - sells
   - closed outcomes
   - win rate
   - take profit count
   - stop loss count
   - realized PnL
   - unrealized PnL
3. Round-trip win-rate calculation.
4. Anomaly warning system.
5. Safe DB initialization/migration for API-read tables.

## Acceptance Tests
Run:
```powershell
python -m py_compile .\services\api.py

Invoke-RestMethod http://127.0.0.1:8080/status
Invoke-RestMethod http://127.0.0.1:8080/trades
Invoke-RestMethod http://127.0.0.1:8080/positions
Invoke-RestMethod http://127.0.0.1:8080/portfolio/latest
```

Pass conditions:
```text
No API 500
No missing table crash
No fake win rate from raw sell count
Duplicate sell rows are flagged as anomaly
sell_count > buy_count is flagged as anomaly in spot mode
```

## Final Report Format
Return:
1. API route summary
2. Metric definition summary
3. Bugs found
4. Patch proposal
5. Test output
6. Remaining dashboard risks
