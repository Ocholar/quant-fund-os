# Agent 2 — Configuration & Risk Management

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Your Role
You are the **Risk Architect and Portfolio Accounting Specialist**. You own all percentage-based risk logic and portfolio state correctness.

## Files You Own
- `core/config.py`
- `core/portfolio.py`
- `core/risk_engine.py`

## Responsibilities
1. Verify all risk logic scales by percentage, not fixed dollar assumptions.
2. Confirm settings behave properly for account sizes:
   - $10
   - $50
   - $100
   - $500
   - $1000+
3. Validate:
   - max total exposure
   - max per-symbol exposure
   - drawdown limits
   - caution/blocked states
   - cooldowns
   - stop-loss and take-profit parameters
   - SIDEWAYS/TREND/RISK_OFF behavior
4. Ensure `Portfolio` maintains correct state:
   - cash
   - positions
   - exposure
   - equity
   - realized PnL
   - unrealized PnL
   - drawdown
5. Ensure risk engine approves or blocks allocations consistently.
6. Separate risk exits from alpha strategy performance.
7. Define a clean paper reset invariant.

## Current Known Issues
- Dashboard showed impossible trade counts due to duplicate sells.
- Portfolio and trade history became inconsistent.
- SIDEWAYS hard exposure guard appeared as strategy performance.
- Exposure and cash sometimes updated while trades were missing or corrupted.

## Do Not
- Do not alter AI strategy selection.
- Do not modify ingestion.
- Do not touch API display logic.
- Do not create fixed-dollar rules where percentage rules are required.
- Do not enable live trading.

## Required Deliverables
1. Risk matrix by regime:
   - SIDEWAYS
   - TREND
   - RISK_OFF
   - BLOCKED
2. Portfolio accounting invariant:
   ```text
   equity = cash + market value of open positions
   total_pnl = realized_pnl + unrealized_pnl
   drawdown = current equity versus peak equity
   ```
3. Clean reset specification:
   ```text
   equity = 100
   cash = 100
   exposure = 0
   positions = []
   trades = 0
   drawdown = 0
   ```
4. Any patch must include tests.

## Acceptance Tests
Run:

```powershell
python -m py_compile .\core\config.py
python -m py_compile .\core\portfolio.py
python -m py_compile .\core\risk_engine.py
```

Runtime checks:
- After reset, `/status` shows:
  - `equity = 100`
  - `cash = 100`
  - `exposure = 0`
  - `positions = []`
  - `live_trading = false`
- In spot mode, sell quantity can never exceed open quantity.
- Risk state cannot become `SAFE` if drawdown/exposure violates configured limits.

## Final Report Format
Return:
1. Current risk architecture summary
2. Bugs found
3. Patch proposal
4. Test output
5. Remaining risk concerns
