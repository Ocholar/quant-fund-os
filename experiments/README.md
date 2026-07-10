# Experiment Framework

This directory stores structured experiment run snapshots.

## Structure

```
experiments/
├── template.md          ← Copy this for each new experiment
├── EXP_001_example/     ← One folder per run
│   ├── config.json      ← Config snapshot at time of run
│   ├── edge_report.json ← Aggregate edge metrics
│   ├── strategy_report.json
│   ├── trades.csv       ← Canonical Trade Dataset for this run
│   └── notes.md         ← Free-form experiment notes
```

## How to create a run snapshot

```bash
python -m analytics.cli --export-run experiments/EXP_001_my_experiment
```

This will:
1. Build the Canonical Trade Dataset from the current DB state.
2. Compute edge and strategy reports.
3. Write all outputs into the specified directory.
4. Save a copy of relevant config keys.

## Naming convention

`EXP_<NNN>_<short_description>`

Example: `EXP_003_trending_regime_only`
