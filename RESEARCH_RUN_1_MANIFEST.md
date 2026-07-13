# Research Run 1 Baseline Manifest

## Repository
- **Branch**: milestone2a-step21-table-columns
- **Commit SHA**: b430b94b25fa058ff88d50c0f616b086122e616c
- **Tag**: `research-run-1-baseline` (pending creation)
- **Status**: CLEAN (pending cleanup execution)

## Docker Environment
- **Docker Compose Version**: v5.1.4
- **Docker Version**: 29.6.1, build 8900f1d
- **Images**:
  - `quant-fund-os-quant:latest` (ID: e8d58f93d244)
  - `postgres:16` (ID: fe03a7605299)
  - `redis:7` (ID: b2b95679e3b4)
- **Container Names**: `quant-fund-os-quant-1`, `quant-fund-os-postgres-1`, `quant-fund-os-redis-1` (expected based on compose)

## Environment Variables (Trading Configuration)
- **Python Version**: 3.11.9
- **Dependencies**: 
  - fastapi==0.115.6, sqlalchemy==2.0.36, redis==5.2.1, pandas==2.2.3, numpy==2.1.3, psycopg2-binary==2.9.9
- **Configuration Defaults** (secrets redacted):
  - `PAPER_EQUITY_START`: 100.00
  - `PAPER_CASH_START`: 100.00

## Code Checksums (SHA-256)
- **main.py**: `CA7D3E4FF7CEF8BBFC26F049109E013B1669A5FE43BCCCD031FE93586B8FC4B2`
- **observability.py**: `290433D393DCB92C8F3437A36F07A1B2B4C616F6BF06FBF89274AD995E8C44E6`
- **research_auditor.py**: `D5D1F0E529B117B90B376E7127DFFA22BDA7180CE293AB901074AD8B7F5C9CBC`

## Runtime Check
- **Deployment Timestamp**: Pending (Set upon `docker-compose up`)
- **Build Timestamp**: Pending (Set upon `--no-cache` build)
- **Auditor Version**: v1 (Deterministic, Wall-clock excluded)
- **Observability Version**: v2 (Feature Persistence Active)
