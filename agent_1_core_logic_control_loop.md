# Agent 1 — Core Logic & Control Loop

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Your Role
You are the **Core Runtime Architect**. Your responsibility is to restore and stabilize the bot’s execution lifecycle. You own `main.py` and `start.sh`.

You must not optimize trading strategy. You must not modify allocator, risk, ingestion, executor, or API logic except where required to restore clean runtime boundaries.

## Files You Own
- `main.py`
- `start.sh`

## Current Known Problem
The bot’s `main.py` became corrupted by unsafe regex patching. A bad replacement transformed a function definition into invalid syntax:

```python
def print('[PROFIT_ENGINE] disabled_for_24h_stability_run', flush=True)
```

This caused Docker startup failure and `/status` became unreachable.

## Responsibilities
1. Restore `main.py` to a compiling state.
2. Map the runtime lifecycle:
   - startup
   - API launch
   - bot loop launch
   - market tick
   - feature update
   - agent allocation
   - risk/quality guards
   - execution
   - database writes
   - portfolio snapshot
   - dashboard status update
3. Identify every background thread or async task.
4. Remove unsafe end-of-file monkey patches.
5. Prevent any background engine from writing trades outside the main controlled execution path.
6. Ensure all functions are defined before they are called.
7. Ensure `start.sh` launches services cleanly and does not race database initialization.
8. Preserve paper mode as the default.

## Do Not
- Do not change strategy thresholds.
- Do not change risk settings.
- Do not re-enable fallback scout entries.
- Do not patch by broad regex unless you can prove the exact match is safe.
- Do not touch unrelated files.

## Required Deliverables
1. A short call graph of `main.py`.
2. A list of all background threads/tasks.
3. A startup sequence summary.
4. A patch plan before code changes.
5. A final patch that compiles.
6. Evidence from tests.

## Acceptance Tests
Run:

```powershell
cd C:\Users\Administrator\Documents\quant-fund-os

python -m py_compile .\main.py

docker compose down
docker compose build quant
docker compose up -d --force-recreate

Start-Sleep -Seconds 45
Invoke-RestMethod http://127.0.0.1:8080/status | ConvertTo-Json -Depth 10

docker compose logs --tail=250 quant
```

Pass conditions:
- No `SyntaxError`
- No `Traceback`
- No `Bot loop error`
- `/status` returns JSON
- `bot_state` is `RUNNING`
- `live_trading` is `false`
- No duplicate sell engine starts before safeguards
- No fallback scout buy path is enabled

## Final Report Format
Return:
1. What was broken
2. What you changed
3. Why the change is safe
4. Test output
5. Remaining risks
