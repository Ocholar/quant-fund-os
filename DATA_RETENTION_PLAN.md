# Research Run 1 Data Retention Plan

## Estimated Log Volume
Based on empirical run history (e.g., 84 trades over a 24-hour cycle):
- **Candidate Events/Day**: ~5,000 candidate feature snapshots
- **Trade Events/Day**: ~100 trade lifecycle logs
- **JSONL Growth/Day**: ~7 MB
- **JSONL Growth/Week**: ~50 MB
- **JSONL Growth/Month**: ~210 MB

## Disk Usage Estimates
- **7 Days**: ~50 MB
- **14 Days**: ~100 MB
- **30 Days**: ~210 MB

## Current Log Configuration
The system currently appends raw `JSONL` candidates to `logs/candidates/candidates_YYYY-MM-DD.jsonl` and `logs/trades/trades_YYYY-MM-DD.jsonl`.
Since the disk footprint per month is extremely low (~210 MB) relative to typical SSD capacities, and this is a specialized Research Run (expected to run for ~30 days), automatic retention logic inside the application is not required. The volume is completely sustainable.

## Recommended Data Retention Policy
1. **Log Rotation Policy**: Not required. The system naturally partitions logs by day (`YYYY-MM-DD.jsonl`).
2. **Archive Policy**: The PM should execute a monthly script to compress (`gzip`) logs older than 30 days and sync them to an external cold storage bucket (e.g. S3).
3. **Compression Policy**: `gzip` typically achieves 85-90% compression ratios on JSON text logs. A 210 MB month of logs will compress to ~25 MB.

*No code changes to the Quant Fund OS engine are required to implement this policy. It should be handled externally via cron or CI/CD pipelines.*
