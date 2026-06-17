# Agent 5 Execution/Accounting Chat Summary

## Project

**Quant Fund OS** — paper-first autonomous crypto trading bot.

## Agent Role

**Agent 5 — Order Execution / Execution Accounting**

Agent 5’s responsibility in this thread was to validate and harden the paper-mode execution layer so that BUY/SELL persistence cannot create invalid accounting side effects.

---

## High-Level Context

The broader project had multiple agent workstreams:

- **Agent 1** — Core runtime, Docker, DB stability, main-loop control.
- **Agent 2** — Risk / drawdown gate configuration.
- **Agent 3** — AI allocation and rescue path discipline.
- **Agent 4** — Data ingestion / feature handoff.
- **Agent 5** — Order execution and accounting integrity.
- **Agent 6** — UI / API / dashboard validation.

Agent 5 was repeatedly called back after upstream fixes to validate that execution produced no invalid trade rows, stale positions, negative quantities, duplicate SELLs, or SELL-only lifecycle corruption.

---

## Main Execution/Accounting Problems Investigated

### 1. Duplicate SELL Rows

Earlier runtime data showed impossible paper-mode metrics such as:

```text
buy_count = 6
sell_count = 134
```

Repeated SELL rows appeared for symbols like:

- `IN/USDT`
- `ATLA/USDT`
- `EDEN/USDT`
- `XMR/USDT`
- `ETHFI/USDT`

Strategies involved included:

```text
sideways_green_to_red_exit
sideways_max_hold_profit_engine
sideways_hard_exposure_guard
basket_loss_cap
```

The key bug was that SELLs could be requested repeatedly after a position had already been fully closed.

### 2. Stale Position After Full SELL

A specific failure involved `EDEN/USDT`:

```text
latest trade id 869 = EDEN/USDT full SELL
positions table still showed EDEN/USDT open
Profit Engine kept requesting the same SELL
atomic boundary rejected duplicate_latest_sell repeatedly
```

This revealed that duplicate SELL prevention was working, but full SELL finalization / reconciliation was incomplete.

### 3. Stale Runtime State Causing SELL Storms

After duplicate SELL rows were blocked, upstream engines still repeatedly requested invalid duplicate SELLs.

Examples:

```text
XMR/USDT duplicate_latest_sell: 85 hits
ETHFI/USDT duplicate_latest_sell: 11 hits
SELL_VALIDATION_REJECT storm: 96 hits
```

Root cause narrowed to stale Profit Engine / watchdog / paper sync state continuing to treat already-closed symbols as open.

### 4. `paper_position_sync` Resurrecting Closed Positions

Even after SELL persistence was hardened, stale position sync kept restoring closed positions into the DB with positive quantity.

Symbols affected included:

- `ETHFI/USDT`
- `BSB/USDT`
- `XMR/USDT`
- `NEAR/USDT`
- `GUA/USDT`
- `EDEN/USDT`
- `DASH/USDT`
- `HYPE/USDT`

This created the risk that stale positions could later be sold, producing SELL-only rows after a clean reset.

### 5. SELL-Only Lifecycle After Clean Reset

A clean reset later produced invalid rows like:

```text
buy_count = 0
sell_count = 5
strategy = sideways_hard_exposure_guard
is_exit = false
exit_reason = null
```

This was invalid for spot paper trading because every SELL should be an exit/reduction of a valid prior BUY lifecycle.

### 6. Missing Exit Accounting Columns

The runtime `trades` table initially lacked:

```text
is_exit
exit_reason
```

This caused validation scripts to fail with:

```text
sqlite3.OperationalError: no such column: is_exit
```

Agent 5 added schema guards to ensure these fields exist.

### 7. Runtime DB Instability

The largest final blocker became recurring SQLite access failure inside Docker:

```text
sqlite3.OperationalError: unable to open database file
```

This appeared across many components:

```text
[BIG_LOSS_COOLDOWN] ensure_table_error=unable to open database file
[OPPORTUNITY_MODE] state_error=unable to open database file
[QFOS_AUTO_STALE_RECONCILER_ERROR] error=OperationalError('unable to open database file')
[PORTFOLIO_RECONCILER] error=unable to open database file
[PROFIT_ENGINE] error=unable to open database file
[ACTIVE_POSITION_WATCHDOG] error=unable to open database file
[EMERGENCY_BASKET_WATCHDOG] error=unable to open database file
```

Even after Agent 1 reported DB stability passes, Agent 5’s 20-minute validation runs repeatedly reproduced runtime DB failures.

---

## Key Fixes Introduced During Agent 5 Work

### Atomic Fill Persistence Boundary

A core atomic persistence boundary was introduced / hardened:

```python
qfos_persist_fill_atomic(conn, fill, source="...")
```

This became the single controlled path for persisting paper-mode fills.

It was intended to ensure:

- SELL validation happens before trade row insertion.
- Position quantity is updated atomically.
- Realized PnL is based on DB `avg_entry`.
- Trade rows are inserted only after valid position updates.
- Invalid SELLs create no trade rows.

### Duplicate SELL Guard

Agent 5 added duplicate SELL protection inside the atomic boundary.

Invariant:

```text
If latest trade is already a full SELL and no newer BUY exists,
then repeated SELL must be rejected and no trade row inserted.
```

### Full SELL Reconciliation

Agent 5 added logic to handle stale cases where:

```text
latest trade = SELL
positions.quantity > 0
requested SELL matches the stale open quantity
```

Instead of inserting another SELL, the boundary reconciles the position:

```text
quantity = 0
exposure = 0
unrealized_pnl = 0
no new trade row
```

### Closed-Symbol Tombstone Guard

A closed-symbol tombstone mechanism was added so that once a symbol is fully sold, future stale restores cannot lead to repeated SELL rows.

Key behavior:

- Full SELL sets tombstone.
- New BUY clears tombstone.
- SELL on tombstoned symbol is rejected/reconciled.
- Stale restored position is zeroed again without new trade row.

### DB-Level Stale Closed-Position Reconciler

Agent 5 added:

```python
qfos_reconcile_stale_closed_positions()
```

It scans for positions where:

```text
positions.quantity > 0
latest trade for symbol = SELL
latest SELL quantity covers open quantity
```

Then zeroes the stale position with no trade insertion.

### Automatic Stale Position Reconciler Daemon

Agent 5 added a background reconciler:

```python
qfos_start_stale_position_reconciler_daemon()
```

It runs periodically to clean stale closed positions that are resurrected by runtime sync.

### Exit Accounting Enforcement

Agent 5 added logic so that every accepted paper SELL is persisted as an exit:

```text
is_exit = true
exit_reason = populated
```

For example:

```text
sideways_hard_exposure_guard
```

must become:

```text
is_exit = 1
exit_reason = sideways_hard_exposure_guard
```

### Schema Guard

Agent 5 added startup/schema guard logic to ensure:

```text
trades.is_exit
trades.exit_reason
```

exist in the live DB.

### No-BUY Lifecycle SELL Guard

Agent 5 added a guard that rejects any SELL if the DB does not contain a valid prior BUY lifecycle for that symbol.

This fixed the clean-reset bug where stale `paper_position_sync` positions could be sold into SELL-only rows.

Behavior:

```text
SELL with no prior BUY lifecycle -> rejected
stale position -> zeroed
trade row -> not inserted
```

---

## Validation Results Across Phases

### Phase 1 / 1B — Duplicate SELL Protection

Result: **PASS locally and partially at runtime**

Confirmed:

```text
No negative positions
Duplicate SELL guard present
Duplicate SELL rows blocked
```

But runtime later exposed stale position mismatch after accepted SELL.

### Phase 2B — SELL Finalization and EDEN Reconciliation

Result: **PASS**

Confirmed:

```text
EDEN/USDT quantity = 0
exposure = 0
unrealized_pnl = 0
DUPLICATE_SELL_PATTERNS_RECENT = NONE
NEGATIVE_POSITIONS = NONE
```

SQLAlchemy compatibility issue was also fixed.

### Phase 2D — Duplicate SELL Request Storm Source

Result: **FAIL / partial**

Cleanup fired but showed:

```text
cleared=none
```

XMR/ETHFI still had nonzero DB quantities, so the Profit Engine / watchdog continued retrying.

### Phase 2E — Duplicate Latest SELL Reconciliation

Result: **Local PASS, runtime FAIL**

Local reconciliation worked, but runtime created new ETHFI duplicate SELL rows, meaning stale sync was restoring quantity before each SELL.

### Phase 2F — Tombstone Guard

Result: **Partial PASS**

New duplicate SELL rows stopped during observation, but ETHFI still remained open in `positions`, meaning no new SELL request occurred to trigger reconciliation.

### Phase 2G — DB Stale Position Reconciler

Result: **Stale-position repair PASS, runtime incomplete**

Manual repair reconciled:

```text
ETHFI/USDT
BSB/USDT
```

But later runtime restored them again.

### Phase 2H — Automatic Reconciler Daemon

Result: **PASS**

Confirmed:

```text
NEW_TRADES_AFTER_PHASE2H: NONE
NEW_DUPLICATE_SELL_PATTERNS_AFTER_PHASE2H: NONE
NEGATIVE_POSITIONS: NONE
OPEN_POSITIONS: NONE
```

The daemon repaired ETHFI automatically:

```text
[QFOS_DB_STALE_POSITION_RECONCILED] symbol=ETHFI/USDT
[QFOS_AUTO_STALE_RECONCILER] reconciled=ETHFI/USDT
```

### Phase 3A — Execution Accounting Fix

Result: **Local PASS, runtime schema FAIL**

Local tests passed, but live DB initially lacked `is_exit` and `exit_reason`.

### Phase 3A2 — Schema Guard + Stale Position Write Block

Result: **Partial PASS / execution still dirty**

Schema was fixed and SELLs were marked as exits, but SELL-only lifecycle still appeared because stale `paper_position_sync` positions existed without BUY lifecycle.

### Phase 3A3 — BUY-Lifecycle Guard

Result: **Execution/accounting trade integrity PASS, full runtime cleanliness FAIL**

Confirmed:

```text
NEW_TRADES_AFTER_PHASE3A3: NONE
SELLS_WITH_BAD_EXIT_ACCOUNTING: NONE
SELL_ONLY_LIFECYCLE_CHECK: buys 0 sells 0
NEW_DUPLICATE_SELL_PATTERNS: NONE
NEGATIVE_POSITIONS: NONE
OPEN_POSITIONS: NONE
```

But runtime logs still showed repeated stale sync cleanup and bot loop errors.

---

## Agent 3 Rescue Fix Validation Attempts

After Agent 3 fixed the legacy `main.py` `ALLOCATOR_RESCUE` bypass, Agent 5 was asked to validate execution/accounting integrity.

Target invariant:

```text
proposed_fills > 0 but final_applied_fills = 0
must produce no trade rows, no position mutation, no cash/exposure mutation.
```

Agent 3 had reported a valid rescue candidate:

```text
symbol: BSB/USDT
feature_source: NORMAL
ready: True
strategy: evo_2438
confidence: 0.9000
signal_strength: 0.02397
symbol_regime: SYMBOL_BREAKOUT_UP
entry_reason: evo_allocator_rescue_normal_top_quality
```

But downstream exposure guard blocked it:

```text
[PROFIT_ENGINE_GUARD] ENTRY_BLOCKED regime=SIDEWAYS exposure_pct=0.0580 limit=0.0450 blocked=['BSB/USDT']
[EXECUTION_STAGE] begin_apply proposed_fills=0
[EXECUTION_STAGE] final_applied_fills=0
```

Agent 5 could not fully certify this path because runtime DB access repeatedly failed during validation windows.

---

## Final DB Stability Attempts

Agent 1 was repeatedly marked PASS for DB repair, but Agent 5 validation continued to reproduce runtime DB failures.

### DB_OK Initially Passed

Agent 5 confirmed:

```text
DB_OK
```

Initial `/status` baseline also passed:

```text
equity = 100.0
cash = 100.0
exposure = 0.0
positions = []
total_trades = 0
buy_count = 0
sell_count = 0
live_trading = false
bot_state = RUNNING
```

### Post-Observation DB Failed Again

After 20-minute observation, DB inspection failed with:

```text
sqlite3.OperationalError: unable to open database file
```

Runtime logs again showed:

```text
unable to open database file
OperationalError('unable to open database file')
```

across multiple components.

This happened even when DB_OK passed before and after observation in isolated one-line probes.

---

## Latest Agent 5 Final Verdict in This Chat

**FAIL / BLOCKED**

Reason:

```text
Runtime SQLite access is still unstable during active bot runtime.
```

Agent 5 cannot certify execution/accounting invariants while the runtime DB cannot be opened reliably during the same supervised validation window.

---

## What Was Confirmed in the Latest Run

Confirmed:

```text
main.py compile: PASS
initial DB_OK: PASS
/status before observation: PASS
/status after observation: PASS
equity = 100
cash = 100
exposure = 0
trades = 0
buy_count = 0
sell_count = 0
host DB precheck: clean
trades schema includes is_exit and exit_reason
no negative positions before observation
visible EXECUTION_STAGE logs showed proposed_fills=0 and final_applied_fills=0
```

Not certified:

```text
final_applied_fills=0 creates no DB side effects
blocked BUYs do not mutate cash/exposure/positions
SELL-only lifecycle prevention during active runtime
protective SELL accounting during active runtime
duplicate full exit prevention during active runtime
stale paper_position_sync cannot become executable during active runtime
```

because DB access failed during post-runtime DB validation.

---

## Files / Functions Involved

Primary file repeatedly patched:

```text
main.py
```

`executor.py` was repeatedly expected by PM tasks but was not found at repo root during validations.

Major functions / controls introduced or referenced:

```python
qfos_persist_fill_atomic()
_qfos_duplicate_sell_guard()
_qfos_reconcile_position_from_duplicate_latest_sell()
_qfos_reject_or_reconcile_tombstoned_sell()
qfos_reconcile_stale_closed_positions()
qfos_start_stale_position_reconciler_daemon()
_qfos_exit_accounting_fields()
_qfos_assert_sell_exit_accounting()
qfos_ensure_execution_accounting_schema_and_guards()
qfos_reconcile_positions_without_buy_lifecycle()
```

Important strategy / exit labels involved:

```text
sideways_hard_exposure_guard
sideways_green_to_red_exit
sideways_max_hold_profit_engine
basket_loss_cap
paper_position_sync
```

---

## Required Execution Invariants

Agent 5’s target invariants throughout the thread were:

1. No DB BUY row unless fill is in `final_applied_fills`.
2. No DB SELL row unless valid open quantity exists.
3. No SELL quantity greater than open quantity.
4. No duplicate full-position SELL after closed.
5. No negative positions.
6. No stale `paper_position_sync` position should become executable.
7. Protective SELLs must be marked as `is_exit=true` with `exit_reason` populated.
8. `final_applied_fills=0` must not mutate trades, positions, cash, or exposure.
9. SELLs without prior BUY lifecycle must be rejected.
10. Runtime must have no `OperationalError`, `Traceback`, `Bot loop error`, or `unable to open database file`.

---

## Current Recommendation to PM

Agent 5 should **not** pass execution/accounting validation yet.

Return to:

```text
Agent 1 — runtime DB / Docker / SQLite stability owner
```

Agent 1 must prove DB stability under the exact same Agent 5 runtime validation conditions, not just with isolated DB_OK probes.

Minimum Agent 1 acceptance should include:

```text
15–20 minute active runtime observation
post-runtime sqlite DB inspection succeeds
unable to open database file count = 0
OperationalError count = 0
Traceback count = 0
Bot loop error count = 0
/status remains reachable
DB_OK passes before, during, and after observation
```

Only after that should Agent 5 rerun execution/accounting validation.

---

## Current PM Decision Recommendation

```text
Agent 5: FAIL / BLOCKED
Reason: runtime DB instability prevents certification.
Next owner: Agent 1
Do not proceed to Agent 6 yet.
Do not return to Agent 3 unless allocation behavior changes again.
```

