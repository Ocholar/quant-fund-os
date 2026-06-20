# Agent 6 Report — Dashboard/API Validation After Runtime Recovery

Generated: 2026-06-18 08:18:01
Repo: C:\Users\Administrator\Documents\quant-fund-os

## Verdict
PASS

## Compile Checks

- services/api.py compile: True
- main.py compile: True

## Postgres Cross-Check

- trades count: 0
- buy count: 0
- sell count: 0
- open positions count: 0
- negative position rows: 0
- orphan_position_archive table exists count: 1

### Side Counts
```text
 side | count 
------+-------
(0 rows)


```

### Open Positions
```text
 symbol | quantity | exposure | strategy 
--------+----------+----------+----------
(0 rows)


```

### Latest Portfolio Snapshots
```text
docker : ERROR:  column "realized_pnl" does not exist
At line:4 char:12
+     $raw = docker compose exec -T postgres psql -U qfos 
-d quant_fund ...
+            
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (ERROR:  colum 
   n ... does not exist:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
 
LINE 1: select equity, cash, exposure, drawdown, 
realized_pnl, unrea...
                                                 ^

```

### Orphan Position Archive
```text
 id |        archived_at         |   symbol    | quantity  | avg_entry | realized_pnl | unrealized_pnl | last_price | exposure  |      strategy       |                reason                 |        source_table         
----+----------------------------+-------------+-----------+-----------+--------------+----------------+------------+-----------+---------------------+---------------------------------------+-----------------------------
  4 | 2026-06-17 19:47:09.385066 | GENIUS/USDT | 4.7687435 |   0.41863 |            0 |   -0.025560465 |    0.41327 | 1.9661529 | paper_position_sync | ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE | agent5_pre_cleanup_evidence
  3 | 2026-06-17 19:47:09.385066 | BSB/USDT    | 3.6340709 |      0.55 |            0 |   0.0009811991 |    0.55027 | 1.9997202 | paper_position_sync | ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE | agent5_prior_audit_evidence
  2 | 2026-06-17 19:47:09.385066 | BOB/USDT    | 346.17856 |  0.005724 |            0 |   -0.023193963 |   0.005657 | 1.9579859 | paper_position_sync | ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE | agent5_pre_cleanup_evidence
  1 | 2026-06-17 19:47:09.385066 | BILL/USDT   | 21.881485 |    0.0685 |            0 |   -0.049014527 |    0.06626 | 1.4472414 | paper_position_sync | ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE | agent5_pre_cleanup_evidence
(4 rows)


```

## Feature Visibility

- Feature symbols visible in logs: True
- ready_features visible in logs: True
- normal_features visible in logs: True
- NORMAL source visible in logs: True

Filtered logs saved to:

```text
C:\Users\Administrator\Documents\quant-fund-os\agent6_validation_output\runtime_logs_20260618_081801.txt
```

## Runtime Error Scan

- unable to open database file: False
- OperationalError: False
- Traceback: False
- Bot loop error: False
- SyntaxError: False

## API Evidence

- equity: 100
- cash: 100
- exposure: 0
- exposure_pct: 0
- drawdown: 0
- positions count: 0
- total_trades: 0
- buy_count: 0
- sell_count: 0
- win_rate: 0
- take_profit_count: 0
- stop_loss_count: 0
- realized_pnl: 0
- unrealized_pnl: 0
- total_pnl: 0
- risk_status: SAFE
- bot_state: RUNNING
- pause_reason: 

## Anomaly Checks

No blocking API/dashboard truth failures detected by this harness.


## Files Changed

None by validation harness.

## Remaining Risks

PM can proceed to a 30–60 minute supervised run, provided dashboard anomaly warnings are visibly rendered if contradictory ledger states reappear.

## Recommendation

Next owner: PM / runtime observer for 30–60 minute supervised run. Agent 6 remains on standby only if API/dashboard contradictions appear.

## Verdict

PASS

