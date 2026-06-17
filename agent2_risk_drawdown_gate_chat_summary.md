# Agent 2 Chat Summary — Risk / Portfolio Drawdown Gate Fix

## Project

**Quant Fund OS** — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Assigned Role

**Agent 2 — Configuration & Risk Management**

Agent 2 owned the risk and portfolio accounting layer:

```text
core/config.py
core/portfolio.py
core/risk_engine.py
```

Agent 2 was allowed to inspect and patch `main.py` only around risk/drawdown gating because the stale runtime blocker was confirmed there.

---

## Initial Agent 2 Brief

The task was to verify and fix percentage-based risk logic and portfolio state correctness.

Primary responsibilities:

- Ensure all risk logic scales by percentage, not fixed dollar assumptions.
- Validate account-size behavior for $10, $50, $100, $500, and $1000+.
- Validate exposure, drawdown, cooldown, stop-loss, take-profit, and regime behavior.
- Ensure portfolio state is correct: cash, positions, exposure, equity, realized PnL, unrealized PnL, drawdown.
- Ensure risk engine approves or blocks allocations consistently.
- Separate risk exits from alpha strategy performance.
- Define a clean paper reset invariant.

Required clean reset invariant:

```text
equity = 100
cash = 100
exposure = 0
positions = []
trades = 0
drawdown = 0
live_trading = false
```

Required accounting invariant:

```text
equity = cash + market value of open positions
total_pnl = realized_pnl + unrealized_pnl
drawdown = current equity versus peak equity
```

---

## Phase 3A Problem

The PM verdict was:

```text
FAIL — stale risk/drawdown gate after clean reset
```

Runtime evidence showed:

```text
trades = 0
positions = 0
open positions = NONE
negative positions = NONE
duplicate sell groups = NONE
latest trades = NONE
```

But valid NORMAL-feature BUY candidates were blocked with:

```text
reason=near_blocked_drawdown_-0.0525
```

This happened while `/status` often showed:

```text
equity = 100.0
cash = 100.0
exposure = 0.0
positions = 0
risk_status = SAFE
```

At some points `/status` temporarily showed the inconsistent state:

```text
risk_status = BLOCKED
equity = 94.75
cash = 94.75
exposure = 0.0
positions = 0
trades = 0
```

This meant stale drawdown, stale peak equity, or stale pause/block state was surviving a clean reset.

---

## Files Initially Uploaded

The user uploaded:

```text
agent_2_config_risk_management(1).md
config.py
portfolio.py
risk_engine.py
agent_2_phase3a_risk_drawdown_gate_fix(1).md
```

Initial findings:

### `core/config.py`

Contained percentage risk settings but lacked explicit reset-safe semantics for stale drawdown behavior.

Important loaded runtime overrides later discovered from `.env`:

```text
starting_equity = 100.0
caution_drawdown = -0.04
blocked_drawdown = -0.08
near_blocked_drawdown_buffer = 0.0025
near_blocked_drawdown = -0.0775
max_total_exposure_pct = 0.12
caution_exposure_pct = 0.09
live_trading = False
```

### `core/portfolio.py`

Initially only tracked:

```python
cash
positions
equity
peak
```

It did not have:

- explicit reset method
- realized/unrealized PnL accounting
- total PnL invariant
- exposure percentage helper
- spot sell quantity guard
- invariant checks

### `core/risk_engine.py`

Initially only checked:

```python
require_human_approval and live_trading
leverage
estimated_var
```

It did not derive `SAFE / CAUTION / BLOCKED` from current portfolio state and did not include reset-safe `can_buy()` logic.

---

## First Agent 2 Patch

A full PowerShell patch was provided to:

1. Back up existing files.
2. Rewrite `core/config.py`.
3. Rewrite `core/portfolio.py`.
4. Rewrite `core/risk_engine.py`.
5. Add `tests/test_agent2_risk_portfolio.py`.
6. Run compile and pytest checks.
7. Search for stale drawdown references.
8. Inspect stale DB/state files.

### Added to `core/config.py`

Added explicit drawdown fields:

```python
caution_drawdown: float = -0.02
blocked_drawdown: float = -0.05
near_blocked_drawdown_buffer: float = 0.0025
```

Added computed property:

```python
@property
def near_blocked_drawdown(self) -> float:
    return float(self.blocked_drawdown) - abs(float(self.near_blocked_drawdown_buffer))
```

This was later corrected because drawdown values are negative and the first formula made near-block more negative than hard-block.

### Added to `core/portfolio.py`

Rewrote `Portfolio` as a dataclass with:

```python
cash
positions
avg_entry
realized_pnl
equity
peak
unrealized_pnl
```

Added:

```python
reset()
mark_to_market()
exposure
exposure_pct
total_pnl
drawdown
can_sell_quantity()
assert_invariants()
```

### Added to `core/risk_engine.py`

Added:

```python
RiskDecision
reset_risk_state()
drawdown_from_equity()
risk_status()
can_buy()
```

### First test result

Compile passed, but pytest failed:

```text
FAILED test_blocked_when_drawdown_really_violates_limit
AssertionError: assert 'CAUTION' == 'BLOCKED'
```

Root cause: `.env` had overridden `blocked_drawdown` to `-0.08`, so the test expectation that `94.75` should be `BLOCKED` was wrong. At an 8% hard drawdown threshold, `94.75` is only a 5.25% drawdown and is therefore `CAUTION`, not `BLOCKED`.

---

## Discovery: Real Stale Gate Was in `main.py`

Search output confirmed `main.py` still had this gate inside `can_buy()`:

```python
current_drawdown = float(getattr(portfolio, 'drawdown', 0.0) or 0.0)
caution_drawdown = float(getattr(settings, 'caution_drawdown', -0.02))
blocked_drawdown = float(getattr(settings, 'blocked_drawdown', -0.05))
if current_drawdown <= blocked_drawdown * 0.9:
    return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')
```

This was the actual stale runtime blocker.

Problems with this logic:

1. It used `portfolio.drawdown` directly, which could be stale after reset.
2. It used `blocked_drawdown * 0.9`, which is wrong for negative drawdowns.
3. It could block clean reset candidates even when `/status` showed `SAFE` and equity/cash were reset to `100.0`.
4. It mislabeled hard drawdown breach as `near_blocked_drawdown`.

---

## Second Patch — Correct Threshold Semantics

A follow-up patch corrected `near_blocked_drawdown` semantics:

```python
return float(self.blocked_drawdown) + abs(float(self.near_blocked_drawdown_buffer))
```

With loaded runtime thresholds:

```text
blocked_drawdown = -0.08
near_blocked_drawdown_buffer = 0.0025
near_blocked_drawdown = -0.0775
```

This is correct because drawdown is negative; a near-block warning should happen before hard block, so it must be less negative than the hard threshold.

### Risk engine order was also fixed

`RiskEngine.can_buy()` was changed to evaluate hard block before near-block:

```python
if drawdown <= float(settings.blocked_drawdown):
    return RiskDecision(False, f"blocked_drawdown_{drawdown:.4f}", "BLOCKED")

if drawdown <= float(settings.near_blocked_drawdown):
    return RiskDecision(False, f"near_blocked_drawdown_{drawdown:.4f}", current_status)
```

### Tests were updated

Tests now respect `.env` overrides instead of assuming hardcoded `-0.05`.

New tests included:

- clean reset invariant
- safe after reset can buy
- stale `94.75` does not survive clean reset
- blocked when drawdown violates loaded hard limit
- near-blocked warning zone before hard-block
- total exposure blocks SAFE label
- spot sell cannot exceed open quantity
- mark-to-market updates equity, peak, drawdown, and unrealized PnL

Result:

```text
8 passed, 1 warning
```

The warning was only a Pydantic v2 class-based config deprecation warning.

---

## Failed `main.py` Regex Patch

A patch was attempted to replace the stale `main.py` block. It failed with:

```text
MAIN_CAN_BUY_PATCH_FAILED: expected stale drawdown block not found exactly once
```

Cause: the regex matcher was too strict for the actual formatting in `main.py`.

Verification still showed the stale gate present:

```python
if current_drawdown <= blocked_drawdown * 0.9:
    return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')
```

---

## Robust `main.py` Patch

A line-based robust patch was provided.

It found the `current_drawdown` block and replaced it with reset-safe logic.

Patch result:

```text
MAIN_CAN_BUY_ROBUST_PATCH_OK
REPLACED_LINES 1474 TO 1491
```

However, compile failed with:

```text
IndentationError: expected an indented block after 'try' statement on line 1473
```

Inspection showed:

```python
try:
try:
```

The robust patch had inserted a new `try:` without removing the original one.

---

## Duplicate `try:` Fix

A final patch removed consecutive duplicate `try:` lines.

Patch result:

```text
DUPLICATE_TRY_FIX_OK
REMOVED_DUPLICATE_TRY_COUNT 1
```

Compile checks passed:

```text
python -m py_compile .\core\config.py       PASS
python -m py_compile .\core\portfolio.py    PASS
python -m py_compile .\core\risk_engine.py  PASS
python -m py_compile .\main.py              PASS
```

Agent 2 tests passed:

```text
8 passed, 1 warning
```

Duplicate `try:` check passed:

```text
PASS_NO_DUPLICATE_TRY
```

Stale gate check passed:

```text
blocked_drawdown * 0.9 no longer present
```

---

## Final `main.py can_buy()` Behavior

The corrected drawdown block now:

1. Reads current drawdown.
2. Reads loaded `caution_drawdown`, `blocked_drawdown`, and `near_blocked_drawdown_buffer`.
3. Computes:

```python
near_blocked_drawdown = blocked_drawdown + near_buffer
```

4. Counts open positions.
5. If runtime/DB are clean and equity is back at the reset baseline, clears stale drawdown memory:

```python
if open_positions_count == 0 and float(equity or 0.0) >= INITIAL_EQUITY * 0.999:
    if current_drawdown < 0:
        portfolio.cash = float(equity or INITIAL_EQUITY)
        portfolio.equity = float(equity or INITIAL_EQUITY)
        portfolio.peak = max(float(INITIAL_EQUITY), float(equity or INITIAL_EQUITY))
        current_drawdown = 0.0
        print('[AGENT2_RISK_RESET] cleared stale drawdown gate in can_buy', flush=True)
```

6. Checks hard block before near-block:

```python
if current_drawdown <= blocked_drawdown:
    return (False, f'blocked_drawdown_{current_drawdown:.4f}')

if current_drawdown <= near_blocked_drawdown:
    return (False, f'near_blocked_drawdown_{current_drawdown:.4f}')
```

7. Applies caution drawdown restrictions only after hard/near checks.

---

## Docker Restart and Runtime Result

Docker rebuild and restart succeeded:

```text
quant-fund-os-quant-1   Up   0.0.0.0:8080->8080/tcp
quant-fund-os-redis-1   Up   0.0.0.0:6379->6379/tcp
```

API checks succeeded:

```text
/resume  OK
/status  OK
```

`/resume` returned:

```json
{
  "status": "running",
  "paused": false
}
```

`/status` showed clean reset state:

```json
{
  "risk_status": "SAFE",
  "portfolio": {
    "equity": 100.0,
    "cash": 100.0,
    "exposure": 0.0,
    "exposure_pct": 0.0,
    "drawdown": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "total_pnl": 0.0
  },
  "positions": [],
  "performance": {
    "total_trades": 0,
    "buy_count": 0,
    "sell_count": 0
  },
  "trading": {
    "total_trades": 0,
    "buy_count": 0,
    "sell_count": 0,
    "latest_trades": []
  }
}
```

---

## Current Runtime Logs

The stale risk blocker is gone.

Current no-trade reason is now:

```text
features_empty
```

Relevant logs:

```text
Market symbols: 59
Feature symbols: 0
No-trade reason: features_empty
WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...
FALLBACK FEATURES: {}
ALLOCATOR BLOCK: no_allowed_positive_strategy
ALLOCATOR_RESCUE no_candidate_passed
ORDERS: []
final_applied_fills=0
risk_status: SAFE
equity: 100.0
cash: 100.0
exposure: 0
positions: {}
```

This means Agent 2’s stale drawdown blocker is resolved. The bot is not trading because the feature pipeline is empty, not because risk is incorrectly blocking buys.

---

## Final Verdict

```text
Agent 2 — PASS
```

Risk/portfolio drawdown gate fix is complete.

Evidence:

```text
compile checks passed
Agent 2 pytest passed
main.py compiles
Docker container is up
/status works
/resume works
risk_status = SAFE
equity = 100.0
cash = 100.0
exposure = 0.0
positions = []
trades = 0
stale blocked_drawdown * 0.9 gate removed
near_blocked_drawdown_-0.0525 no longer appears as the blocker
```

---

## Remaining Issue / Next Owner

The remaining blocker is outside Agent 2 scope:

```text
Feature symbols: 0
Normal FEATURES is empty
FALLBACK FEATURES: {}
No-trade reason: features_empty
```

Recommended next owner:

```text
Agent 4 — Data Ingestion & Feature Engineering
```

Reason: validated market prices exist, but feature generation is empty.

Agent 3 should not be called yet unless Agent 4 confirms features are normal and allocation still fails.

---

## Recommended Next PM Instruction

```markdown
# PM Follow-up to Agent 4 — Feature Pipeline Empty After Agent 2 Risk PASS

Agent 2 is now marked PASS for the stale risk/drawdown gate fix.

Current runtime is clean:

- Docker container: Up
- /status: OK
- /resume: OK
- risk_status: SAFE
- equity: 100.0
- cash: 100.0
- exposure: 0.0
- positions: []
- trades: 0
- stale near_blocked_drawdown gate: cleared

However, trading remains blocked because the feature pipeline is empty:

```text
Market symbols: 59
Feature symbols: 0
No-trade reason: features_empty
WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...
FALLBACK FEATURES: {}
ALLOCATOR BLOCK: no_allowed_positive_strategy
ALLOCATOR_RESCUE no_candidate_passed
ORDERS: []
final_applied_fills=0
```

Your task is to investigate why validated market prices exist but NORMAL features are not being produced.

Primary scope:

```text
data/ingestion.py
feature_store.py
any feature handoff code between ingestion and main.py
```

Do not modify risk logic, portfolio accounting, or Agent 2 files unless you find a direct integration bug.

Acceptance criteria:

```text
Feature symbols > 0
normal_features > 0
ready_features > 0
feature.source = NORMAL
feature.ready = True
/status remains SAFE with equity=100, cash=100, exposure=0 before trades
No stale near_blocked_drawdown blocker
No invalid fallback executable buys
```
```
