# Repository Cleanup Plan

## Status Definitions
- **KEEP**: Canonical repository files required for runtime, configuration, observability, and research.
- **ARCHIVE**: Historical artifacts, previous run outputs, and experimental scripts to be compressed into a single zip outside the working tree for record-keeping.
- **DELETE**: Temporary logs, scratch files, cache directories, and obsolete artifacts with no historical engineering value.

---

## 1. DELETE
*No engineering value. Safe to permanently remove.*
- `__pycache__/`
- `.pytest_cache/`
- `scratch/` directory and contents (temporary debugging scripts)
- `logs/` directory contents (temporary logs, `quant.log`, `mc_live.log`, jsonl files)
- `data/feature_history_runtime.json` (temporary state)
- `deep_diagnostic_*.txt`, `stall_diagnostics_*.txt`
- `qfos_expectancy_state.json`, `qfos_expectancy_decisions.jsonl`
- `diff.txt`, `logs.txt`, `docker_logs.txt`, `docker_logs_6h.txt`
- `mc_live.log`, `mission_control_out.log`, `mission_control_err.log`, `mission_control_crash.log`, `qfos_txn_pool.log`

## 2. ARCHIVE
*Historical engineering value. Move to `quant_fund_os_historical_archive.zip` outside working tree, then delete from repo.*
- `agent*_*/` directories (historical soak runs and patch environments)
- `forensic_*/`, `phase2_*/`, `phase3_*/`, `runtime_*/`, `qfos_hard_clean_slate_*/` directories
- `soak_reports/`, `validation_reports/`, `validation_runs/`, `db_backups/`
- All `patch_*.py`, `qfos_*.py`, `apply_*.py`, `phase*_*.py`, `forensic*.py`, `audit_*.py`, `probe_*.py`
- All `.txt` trace and diagnostic files (`*_trace.txt`, `*_diagnostic*.txt`, `signal_root_cause.txt`, etc.)
- All `main.py.*` and `mission_control.py.*` backup files
- `*.sql` database backup scripts (e.g. `postgres_dump_*.sql`, `qfos_before_*.sql`)
- Old reports (`QUALITY_DIAGNOSTICS_REPORT.txt`, `LIVE_SIGNAL_PIPELINE.txt`)

## 3. KEEP
*Canonical files required for Research Run 1.*
- `.env` (configuration)
- `docker-compose.yml`, `docker-compose.override.yml`, `Dockerfile`
- `main.py`, `observability.py`, `feature_store.py`, `mission_control.py`
- `core/`, `infra/`, `execution/`, `data/`, `analytics/`, `docs/`, `tests/` directories
- `research_auditor.py`
- `RESEARCH_RUN_1_PREFLIGHT_REPORT.md`
- `start.sh`, `requirements.txt`
- `.dockerignore`, `.gitignore`

---
*Note: Deletions will only occur upon PM approval of the implementation plan.*
