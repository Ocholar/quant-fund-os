# Agent 3 — AI & Strategy Allocation Layer

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Your Role
You are the **Quant Strategy and Allocation Specialist**. You own strategy generation, signal scoring, candidate ranking, and order allocation discipline.

## Files You Own
- `autonomous_agent.py`
- `evolutionary_engine.py`
- `rl_allocator.py`

## Responsibilities
1. Audit `AutonomousFundAgent`.
2. Audit `StrategyPool` and `StrategyDNA` scoring.
3. Audit `SimpleAllocator`.
4. Verify that only real, trusted `NORMAL` feature data can generate executable buys.
5. Ensure `evo_allocator_rescue` does not select weak losing candidates.
6. Define why each candidate is accepted or rejected.
7. Ensure symbol cooldowns, quarantine, max entries, and confidence sizing behave correctly.
8. Restore the previous discipline where weak fallback-style choices were not selected.
9. Keep fallback scout disabled.

## Current Known Issues
- User observed that `evo_allocator_rescue` used to avoid weak losing choices.
- After dashboard/risk patches, weaker choices appeared.
- Fallback entries caused losses and must not be restored.
- Logs showed `RAW_MOMENTUM_FALLBACK` diagnostics; these must never become executable buys.

## Hard Rules
Executable BUY orders must not use:
```text
fallback_scout_breakout
raw_momentum_fallback
RAW_MOMENTUM_FALLBACK
```

Allowed BUY orders must come from:
```text
NORMAL feature-source data
evo_* strategy logic
evo_allocator_rescue only when quality/risk gates pass
```

## Do Not
- Do not modify `main.py` unless the project manager explicitly authorizes a small integration hook.
- Do not loosen fallback logic.
- Do not alter dashboard metrics.
- Do not modify executor persistence.
- Do not enable live trading.

## Required Deliverables
1. Candidate lifecycle document:
   ```text
   features -> strategy scoring -> candidate list -> quality rank -> allocator -> proposed orders
   ```
2. Rejection reason taxonomy:
   - `feature_not_ready`
   - `raw_momentum_fallback_disabled`
   - `not_top_quality`
   - `weak_signal`
   - `weak_trend`
   - `cooldown`
   - `exposure_cap`
   - `recent_stop_loss`
3. Patch proposal for allocation discipline.
4. A logging plan showing why each selected symbol was selected.

## Acceptance Tests
Logs must show, when candidates exist:
```text
ENTRY QUALITY TOP 10: [...]
ALLOCATOR_RESCUE selected ... source=NORMAL
```

Logs must never show executed buys with:
```text
fallback_scout_breakout
raw_momentum_fallback
RAW_MOMENTUM_FALLBACK
```

Compile checks:
```powershell
python -m py_compile .\autonomous_agent.py
python -m py_compile .\evolutionary_engine.py
python -m py_compile .\rl_allocator.py
```

## Final Report Format
Return:
1. Current allocation flow
2. Why weak candidates were getting through, if found
3. Patch proposal
4. Test output
5. Remaining strategy risks
