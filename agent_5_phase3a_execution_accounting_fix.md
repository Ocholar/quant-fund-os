# Agent 5 Task Brief — Phase 3A Execution/Accounting Integrity Fix

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Assigned Agent
Agent 5 — Order Execution

## PM Verdict
Primary owner: **Agent 5 — Execution**

Secondary support only: **Agent 6 — UI/API**, but do not call Agent 6 until Agent 5 confirms execution/accounting behavior is correct at the source.

## Current Phase
Phase 3A — Clean supervised runtime observation after corrective reset.

## Current Status
**FAIL — execution/accounting integrity issue.**

Do not continue to Phase 3B strategy/allocation tuning.  
Do not call Agent 3 yet.  
The bot is not simply failing to select good entries; it is creating SELL activity from guard logic with invalid lifecycle accounting.

---

## Incident Summary

After a supposed clean reset, the bot produced SELL-only trade activity.

Final observed status:

```text
equity: 99.98
cash: 99.98
exposure: 0.0
positions from /status: 0
total_trades: 5
buy_count: 0
sell_count: 5
live_trading: false
bot_state: RUNNING
risk_status: SAFE
```

The five SELL rows were:

```text
BEAT/USDT   SELL   1.44057969      sideways_hard_exposure_guard
CAKE/USDT   SELL   1.00161083      sideways_hard_exposure_guard
TRIA/USDT   SELL   39.87240829     sideways_hard_exposure_guard
ULTIMA/USDT SELL   0.00085581      sideways_hard_exposure_guard
ZEC/USDT    SELL   0.00339914      sideways_hard_exposure_guard
```

Problem details:

```text
buy_count = 0
sell_count = 5
strategy = sideways_hard_exposure_guard
is_exit = false
exit_reason = null
```

This is invalid for spot paper trading. A hard-exposure guard SELL is an exit/reduction action and must not be recorded as a normal non-exit trade.

---

## Critical Finding

The issue is **not mainly allocation**.

Analyzer evidence showed:

```text
MARKET TICK DATA VALIDATED hits: 385
Feature symbols positive hits: 333
NORMAL/ready feature positive hits: 21587
ENTRY QUALITY TOP 10 empty hits: 478
ENTRY QUALITY TOP 10 nonempty hits: 159
ALLOCATOR_RESCUE hits: 1152
raw_orders nonzero hits: 575
proposed_fills nonzero hits: 320
final_applied_fills nonzero hits: 25
```

The system had market data, features, allocator activity, proposed fills, and applied fills.

The failure is that execution/accounting allowed SELL rows to exist without a clean matching BUY lifecycle.

---

## Your Scope

You own execution/accounting behavior.

Primary files to inspect:

```text
executor.py
main.py execution/fill-application sections only
database trade/position write paths
```

You may inspect `services/api.py` only to understand how trades are displayed, but do not patch dashboard behavior unless PM asks.

---

## Main Questions You Must Answer

1. Why can `sideways_hard_exposure_guard` create SELL trades when `buy_count = 0`?
2. Where are these SELL rows inserted?
3. Are they coming from:
   - executor.py?
   - direct DB writes in main.py?
   - profit/risk guard direct sell functions?
   - portfolio reconciler?
   - stale positions from prior state?
4. Why are these rows marked:
   ```text
   is_exit = false
   exit_reason = null
   ```
5. Why did final `/status` show no positions while DB evidence showed remaining position rows?
6. Is `paper_position_sync` incorrectly rehydrating stale positions into executable sell paths?
7. Is `sideways_hard_exposure_guard` selling positions that were restored from stale DB rows instead of actual current portfolio positions?
8. Does execution reject sells with no valid open quantity?
9. Does execution cap sell quantity to open position quantity?
10. Does execution refuse second full-exit sells after a position is closed?

---

## Hard Invariants You Must Enforce

For spot paper trading:

```text
1. Cannot sell a symbol unless there is open quantity.
2. Cannot sell more than open quantity.
3. Cannot sell the same full position twice.
4. Cannot create a SELL trade row if no valid open position exists.
5. Cannot leave negative position quantity.
6. Every protective SELL must be marked as an exit/reduction.
7. Every protective SELL must have a clear exit_reason.
8. Every accepted fill must create exactly one trade row.
9. Every rejected fill must be logged with a clear reason.
10. Clean reset must produce buy_count=0, sell_count=0, positions=[], exposure=0.
```

---

## Required Fix

Patch execution/accounting only.

You must add source-level protection so that even if upstream logic requests invalid SELLs, execution refuses them.

The correct protection belongs in the execution/fill-application path, not in dashboard display logic.

Expected behavior:

```text
If SELL requested and open quantity <= 0:
    reject fill
    do not write trades row
    log reason=no_open_position

If SELL quantity > open quantity:
    either cap to open quantity or reject
    never write quantity greater than open position

If SELL closes/reduces exposure:
    side=sell
    is_exit=true
    exit_reason=<reason>
    strategy may remain the source strategy, but exit_reason must be populated

If second full SELL arrives after closed:
    reject fill
    do not write trades row
```

---

## Do Not Touch

Do not change:

```text
strategy thresholds
fallback buy logic
evo allocator rescue logic
feature engineering
market data validation
dashboard win-rate display
risk exposure thresholds
live trading settings
```

Do not re-enable:

```text
fallback_scout_breakout
raw_momentum_fallback
RAW_MOMENTUM_FALLBACK executable entries
```

---

## Required Diagnostics

Before patching, produce:

```powershell
cd C:\Users\Administrator\Documents\quant-fund-os

Select-String -Path ".\main.py",".\executor.py" `
  -Pattern "sideways_hard_exposure_guard|INSERT INTO trades|is_exit|exit_reason|apply_sell|apply_buy|PaperExecutor|positions|quantity|sell" `
  -Context 4,4

python -m py_compile .\main.py
python -m py_compile .\executor.py
```

Also inspect database rows:

```powershell
@'
import sqlite3
con = sqlite3.connect("data/quant.db")
cur = con.cursor()

for table in ["trades", "positions", "portfolio_snapshots"]:
    print("\nTABLE", table)
    try:
        print(cur.execute(f"PRAGMA table_info({table})").fetchall())
        print(cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20").fetchall())
    except Exception as e:
        print("ERR", e)

con.close()
'@ | Set-Content ".\qfos_agent5_db_inspect.py" -Encoding UTF8

python .\qfos_agent5_db_inspect.py
```

---

## Acceptance Tests

After your patch, run a clean reset and smoke observation.

### Static tests

```powershell
python -m py_compile .\main.py
python -m py_compile .\executor.py
```

### Clean reset expected state

```text
equity = 100
cash = 100
exposure = 0
positions = []
buy_count = 0
sell_count = 0
trades = 0
```

### Execution unit-style checks

Create or simulate:

```text
1. Sell unknown symbol -> rejected, no trade row.
2. Sell with quantity when position quantity is zero -> rejected, no trade row.
3. Buy 1 TEST/USDT -> position quantity 1, buy row 1.
4. Sell 1 TEST/USDT -> position quantity 0, sell row 1, is_exit=true, exit_reason populated.
5. Sell 1 TEST/USDT again -> rejected, no extra sell row.
6. Buy 1 TEST/USDT -> Sell 2 TEST/USDT -> rejected or capped, never negative.
```

### Runtime observation

Run 15–20 minutes only.

Pass criteria:

```text
No SyntaxError
No Traceback
No Bot loop error
/status works
live_trading=false
bot_state=RUNNING
sell_count cannot exceed valid lifecycle count
No SELL-only rows after clean reset unless there were valid pre-existing open positions and those are explicitly marked as restored/reconciled exits
sideways_hard_exposure_guard SELL rows must be is_exit=true and exit_reason populated
No duplicate full exits
No negative positions
```

---

## Final Report Back to PM

Return your report in this format:

```markdown
# Agent 5 Report — Execution/Accounting Fix

## Verdict
PASS or FAIL

## Root Cause
Explain exactly where invalid SELL-only rows came from.

## Files Changed
List files and functions changed.

## Invariants Added
List execution/accounting protections added.

## Test Results
Paste compile output, DB inspection summary, and 15–20 minute observation summary.

## Remaining Risks
Mention anything that still needs Agent 6 or Agent 1.

## Recommendation
State whether PM can proceed to Agent 6, Agent 3, or another Agent 5 iteration.
```

---

## PM Reminder

You are not being asked to make the bot more profitable yet.  
You are being asked to make trade accounting trustworthy.

No strategy tuning until execution/accounting is clean.
