# Chat Summary — Agent 6 UI Services & Monitoring APIs

## Context
The user uploaded an Agent 6 assignment brief for the Quant Fund OS project. Quant Fund OS is a paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

The uploaded brief defines Agent 6 as the **UI Services & Monitoring APIs** specialist, focused on FastAPI route correctness, dashboard reporting, trade metrics, and anomaly detection.

## Agent Role Confirmed
The assistant accepted the role as:

**Agent 6 — UI Services & Monitoring APIs**

The assistant clarified that Agent 6 owns API and dashboard correctness, not trading strategy or execution behavior.

## Files in Scope
Agent 6 owns:

- `services/api.py`
- Dashboard template/static files, if present

## API Routes in Scope
Agent 6 must audit and harden:

- `/status`
- `/trades`
- `/portfolio/latest`
- `/positions`
- `/quarantine`
- `/dashboard`

## Primary Mission
Agent 6 must prevent the dashboard/API from showing misleading or impossible performance metrics as normal.

The known example issue was:

```text
Buy 3 / Sell 212
```

This is impossible in normal spot-mode accounting and should be flagged clearly as an anomaly rather than presented as valid performance.

## Responsibilities
Agent 6 is responsible for:

1. Auditing `/status`.
2. Auditing `/trades`.
3. Auditing `/portfolio/latest`.
4. Auditing `/positions`.
5. Auditing `/quarantine`.
6. Auditing `/dashboard`.
7. Ensuring missing DB tables cannot crash API routes.
8. Ensuring dashboard metrics are calculated from valid trade history.
9. Ensuring win rate is based on completed round trips, not raw sell rows.
10. Ensuring TP/SL counts include all valid exit strategies.
11. Adding anomaly warnings for impossible states.

## Required Dashboard Anomaly Warnings
The dashboard/API should flag:

- `sell_count > buy_count` in spot mode
- Negative position quantity
- Duplicate full exit rows
- Open exposure with no positions
- Positions with no matching buy
- Realized PnL inconsistent with trade history
- Missing DB tables

## Explicit Non-Scope / Do Not Modify
Agent 6 must not:

- Hide corrupted rows silently
- Modify trading strategy
- Modify executor behavior
- Enable live trading
- Make dashboard metrics look profitable unless trade accounting supports it
- Touch alpha logic, allocation, entry/exit logic, or paper/live mode settings

## Required Deliverables
Agent 6 must produce:

1. API schema contract
2. Dashboard metric definitions for:
   - Total trades
   - Buys
   - Sells
   - Closed outcomes
   - Win rate
   - Take profit count
   - Stop loss count
   - Realized PnL
   - Unrealized PnL
3. Round-trip win-rate calculation
4. Anomaly warning system
5. Safe DB initialization/migration for API-read tables

## Acceptance Tests
The required PowerShell tests are:

```powershell
python -m py_compile .\services\api.py

Invoke-RestMethod http://127.0.0.1:8080/status
Invoke-RestMethod http://127.0.0.1:8080/trades
Invoke-RestMethod http://127.0.0.1:8080/positions
Invoke-RestMethod http://127.0.0.1:8080/portfolio/latest
```

## Pass Conditions
The patch passes if:

- There are no API 500 errors
- Missing DB tables do not crash API routes
- Win rate is not faked from raw sell count
- Duplicate sell rows are flagged as anomalies
- `sell_count > buy_count` is flagged as an anomaly in spot mode

## Final Report Format Required
Agent 6 should return:

1. API route summary
2. Metric definition summary
3. Bugs found
4. Patch proposal
5. Test output
6. Remaining dashboard risks

## Next Requested Input
The assistant asked the user to provide either:

```text
services/api.py
```

or the current repository zip.

Once received, Agent 6 would inspect and return either a patch proposal or a full patched `services/api.py`, plus PowerShell validation commands.
