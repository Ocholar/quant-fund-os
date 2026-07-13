# Quant Fund OS - Scientific Research Protocol
**Research Run ID:** RR1-20260714

## Objective
Determine whether the frozen strategy demonstrates positive expectancy under paper trading.

## Hypothesis
- **H0**: Expectancy ≤ 0
- **H1**: Expectancy > 0

## Frozen Parameters
The following parameters must remain strictly unchanged throughout Research Run 1:
- Signal thresholds (entry and exit)
- Allocator logic and risk engine sizing
- Feature generation and mapping
- Target universe
- Watchdog intervention criteria

## Success Metrics
- **expectancy**: Minimum > 0
- **profit factor**: Minimum > 1.0
- **Sharpe**: Minimum > 1.0
- **drawdown**: Maximum bounded within acceptable limits
- **win rate**: Minimum acceptable threshold based on R-multiple
- **ranking calibration**: Feature values logically correlate with allocator priority
- **signal calibration**: Signal outputs accurately capture predictive regime logic
- **feature correlation**: Observable relation between candidate snapshots and trade exits

## Failure Conditions
The run must be immediately aborted and marked failed if any of the following occur:
- Infrastructure regression (e.g. database stalls, unhandled exceptions)
- Telemetry corruption (e.g. missing snapshots, broken UUID chains)
- Negative expectancy after statistically significant sample size
- Insufficient trades to determine significance

## Stopping Criteria
Research Run ends when:
- 150 completed trades
- **OR** 30 calendar days have elapsed
- **OR** a critical infrastructure regression is confirmed
