# Agent 4 — Data Ingestion & Feature Engineering

## Project
Quant Fund OS — paper-first autonomous crypto trading bot for MEXC spot USDT pairs.

## Your Role
You are the **Market Data and Feature Quality Specialist**. You own the quality of price data and technical features.

## Files You Own
- `data/ingestion.py`
- `feature_store.py`

## Responsibilities
1. Audit `PaperMarketData`.
2. Confirm market data comes from real MEXC/CCXT/direct REST sources.
3. Reject synthetic anomalies, impossible prices, and extreme jumps.
4. Define trusted-symbol warmup rules.
5. Ensure `FeatureStore` maintains correct price history buffers.
6. Verify calculation of:
   - moving averages
   - short trend
   - long trend
   - momentum
   - one-tick momentum
   - volatility
   - signal strength
   - per-symbol regime
7. Investigate why `NORMAL FEATURES` sometimes becomes empty.
8. Ensure `RAW_MOMENTUM_FALLBACK` is diagnostic only and cannot become executable.

## Current Known Issues
Logs repeatedly showed:
```text
WARNING: Normal FEATURES is empty. Trying RAW_MOMENTUM_FALLBACK...
```

This may be acceptable as a diagnostic, but not as a trading source. The project needs to know why normal features are empty and whether feature warmup is too strict.

## Hard Rules
- Real MEXC-derived features may trade.
- `RAW_MOMENTUM_FALLBACK` must never trade.
- Invalid price ticks must not create entries.
- Exits may use last-good prices only under clearly logged rules.

## Do Not
- Do not modify strategy allocation.
- Do not modify order execution.
- Do not modify API metrics.
- Do not enable fallback buy logic.
- Do not enable live trading.

## Required Deliverables
1. Market data health checklist:
   - price source
   - trusted tick count
   - rejected symbol count
   - large jump count
   - normal feature count
   - ready symbol count
2. Feature readiness report.
3. Explanation for empty normal features.
4. Patch proposal if warmup/feature logic is too strict or broken.
5. Contract:
   ```text
   RAW_MOMENTUM_FALLBACK = diagnostic only, never executable
   ```

## Acceptance Tests
Compile:
```powershell
python -m py_compile .\data\ingestion.py
python -m py_compile .\feature_store.py
```

Runtime logs should show after warmup:
```text
trusted_count > 0
normal_feature_count > 0
ready_symbols > 0
```

If normal features are empty, logs must explain why.

## Final Report Format
Return:
1. Data source summary
2. Feature pipeline summary
3. Bugs found
4. Patch proposal
5. Test output
6. Remaining data-quality risks
