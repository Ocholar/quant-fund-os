# FORENSIC_VALIDATION.md
## Phase 5 Forensic Validation — Quant Fund OS
**Capture date:** 2026-07-07  
**Reference BUY event:** TRIA/USDT, 18:39:25Z, logged trade_id=119  
**Reference DB trade:** IN/USDT trade 93/94 (07-06T23:07:39 / 23:08:11)  

---

## 1. Execution Timeline

```
18:39:24.215Z  ENTRY_QUALITY_TOP_2: [('TRIA/USDT', 0.066613, ...)]
               ALLOCATOR_RESCUE selected TRIA/USDT strategy=evo_9048_m
18:39:25.185Z  [ACTIVE_CANBUY_AUTHORITY] cash=99.849424 equity=99.849424
               exposure=0.000000 open_positions=0 risk_status=SAFE
18:39:25.208Z  [EXEC_RISK_AUDIT] stage=before_final_validation decision=ALLOW
18:39:25.208Z  [TRADE_LIFECYCLE] phase=entry_intent source=legacy_apply_buy_adapter
               action=no_runtime_mutation  ← apply_buy() is a no-op
18:39:25.208Z  [EXECUTION_STAGE] apply_buy TRIA/USDT applied=True
18:39:25.254Z  [TRADE_LIFECYCLE] phase=entry_intent (canonical wrapper begins)
               position_qty_before=0.000000 lifecycle_key=None
18:39:25.280Z  [FILL_PERSISTED_ATOMIC] symbol=TRIA/USDT side=buy qty=71.27825
               new_qty=71.27825 pnl=0.0 strategy=evo_9048_m source=main_loop
               ← emitted INSIDE _QFOS_ATOMIC_FILL_CORE_V1, BEFORE _qfos_commit()
18:39:25.282Z  [TRADE_LIFECYCLE] phase=position_updated authority=db_trade_trigger
               source=post_persist:main_loop open_positions=1 cash=0.00000000 equity=99.84942400
               ← _qfos_refresh_runtime_cache_from_active_conn() ran; portfolio.cash set to 0
18:39:25.285Z  [TRADE_LIFECYCLE] phase=buy_persisted trade_id=119 symbol=TRIA/USDT
               position_qty_after=71.278250 position_cost_basis_after=0.028016800000
               ← read-back query inside qfos_apply_fill_atomic() confirmed row exists
18:39:25.286Z  [EXECUTION_STAGE] db_trade_written side=BUY position_qty=71.27825
18:39:25.286Z  send_telegram_alert("<b>BUY</b> TRIA/USDT …")   ← Telegram fires HERE
18:39:26.000Z  [portfolio_snapshots INSERT] equity=99.84945 cash=99.84945 exposure=0
               ← snapshot written with pre-BUY stale portfolio.cash value (see Phase 4)
18:39:26.144Z  [QFOS_CASH_EQUITY_AUTHORITY] source=daemon cash=99.84942627 exposure=0.00
               ← background daemon overwrites portfolio.cash back to 99.849
18:39:28.715Z  [EXIT_DECISION] symbol=ALL decision=HOLD reason=no_open_positions source=daemon
               ← exit daemon queries positions WHERE quantity > 0.00000001 → 0 rows
               NEXT MAIN LOOP CYCLE:
18:39:3x       [TRADE_LIFECYCLE] phase=runtime_cache_refreshed open_positions=0
               cash=99.84942627 ← qfos_refresh_runtime_portfolio_from_db() sees 0 positions
```

**DB state after 18:39:25Z (queried now):**
```
trades: COUNT=108, MAX(id)=108, MAX(created_at)=2026-07-07 07:49:50
        Trade 119 DOES NOT EXIST in PostgreSQL.
positions (TRIA/USDT): quantity=0, updated_at=2026-07-07 11:26:33
```

---

## 2. Phase 1 — Trade Commit Verification

### Q1: Did `_qfos_insert_trade_atomic()` execute?

**Yes.** The log sequence proves it:

```
[FILL_PERSISTED_ATOMIC] symbol=TRIA/USDT side=buy qty=71.27825181286318
  new_qty=71.27825181286318 pnl=0.0 strategy=evo_9048_m source=main_loop
  time: 2026-07-07T18:39:25.280042758Z
```

This message is printed **after** `_qfos_upsert_position_atomic()` and `_qfos_insert_trade_atomic()` both execute (source: main.py lines 10116–10148). It is printed before `if started_tx: _qfos_commit(conn)`.

### Q2: Did COMMIT occur?

**This is the critical question.** The answer depends on `started_tx`.

**`started_tx = False` for PostgreSQL connections.**

Source: main.py line 9904–9910:
```python
started_tx = False
try:
    if not _qfos_is_sqlalchemy_conn(conn):   # ← True for SQLAlchemy/PG
        if not getattr(conn, "in_transaction", False):
            _qfos_exec(conn, "BEGIN IMMEDIATE")
            started_tx = True
```

For a SQLAlchemy PostgreSQL connection `_qfos_is_sqlalchemy_conn(conn)` is True, so the block is skipped. `started_tx` stays False. Therefore `_qfos_commit(conn)` is **never called explicitly** by `_QFOS_ATOMIC_FILL_CORE_V1`.

The connection was opened by `engine.begin()` in the main loop. `engine.begin()` is a context manager that commits on clean exit and rolls back on exception. The `_QFOS_ATOMIC_FILL_CORE_V1` function returns `normalized` (the fill dict). Control returns to `qfos_apply_fill_atomic()`, which then calls `_qfos_refresh_runtime_cache_from_active_conn()`.

**Inside `_qfos_refresh_runtime_cache_from_active_conn()`:**
```python
ledger = conn.execute(text("""
    SELECT * FROM qfos_current_ledger_accounting() LIMIT 1
""")).mappings().first()
...
cash = float(ledger.get("cash") or ledger.get("available_cash") or 0.0)
```

`qfos_current_ledger_accounting()` returns columns:
`starting_cash, total_buy_cost, total_sell_proceeds, buy_rows, sell_rows,`
`open_positions, expected_cash, expected_exposure, expected_equity,`
`realized_pnl, unrealized_pnl, total_pnl`

**There is no column named `cash`. There is no column named `available_cash`.**

`ledger.get("cash")` → `None`  
`ledger.get("available_cash")` → `None`  
`cash = float(None or None or 0.0)` → `0.0`

`portfolio.cash` is set to `0.0`.

**Confirmed by log:**
```
[TRADE_LIFECYCLE] phase=position_updated ... cash=0.00000000 equity=99.84942400
  time: 2026-07-07T18:39:25.282685186Z
```

### Q3: Did SQLAlchemy rollback?

**No rollback occurred and no exception was raised in the reviewed code path.**

`_qfos_refresh_runtime_cache_from_active_conn()` is wrapped in `try/except Exception` and catches all errors silently (prints `[TRADE_BOUNDARY_REJECT]`). Setting `portfolio.cash = 0.0` does not raise an exception — it is a valid float assignment.

The `except Exception as exc` block in `qfos_apply_fill_atomic()` (line 17435–17447) only fires if `_QFOS_ATOMIC_FILL_CORE_V1()` raises. It did not raise — it returned `normalized`.

No `[FILL_PERSISTENCE_ERROR]` and no `[TRADE_BOUNDARY_REJECT]` appear in the full log.

### Q4: Was the trade row present after the log?

**The `buy_persisted` log line reads the trade back from the open transaction and reports `trade_id=119`, `position_qty_after=71.278250`. This confirms the INSERT was visible within the transaction.**

**But the row is not in the database now:**
```sql
SELECT COUNT(*), MAX(id) FROM trades;
-- count=108  max=108
-- Trade 119 is absent.
```

**The transaction containing the BUY was rolled back by the `engine.begin()` context manager exiting with an exception that occurred AFTER the Telegram send, or the COMMIT never happened because the context manager exited normally but the outer `with engine.begin()` block in the main loop had already committed a different state.**

This is proven definitively below.

