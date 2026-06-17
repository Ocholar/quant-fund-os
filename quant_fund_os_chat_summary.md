# Quant Fund OS — Chat Summary and PM Handoff

**Project:** Quant Fund OS  
**Mode:** Paper-first autonomous crypto trading bot  
**Exchange:** MEXC spot USDT pairs  
**Current PM Status:** SQLite runtime DB has been rejected as the active runtime database. The next valid path is a joint Agent 1 + Agent 5 migration from SQLite to Postgres for active runtime state.

---

## 1. Overall Context

This chat covered a supervised recovery and stabilization effort for Quant Fund OS after repeated failures around:

- `/status` API crashes
- missing or broken SQLite schema
- duplicate SELL rows
- bad dashboard metrics
- fallback/rescue entries
- feature generation regressions
- runtime SQLite access instability
- Docker/start.sh failures
- agent-based SwarmBench-style task decomposition

The project was moved from ad hoc patching to a structured PM-led multi-agent workflow.

---

## 2. Key PM Decision

The most important final decision from this chat is:

```text
SQLite is rejected as the active runtime database for Quant Fund OS.
```

Reason:

```text
SQLite repeatedly passed startup DB_OK checks but failed during active runtime with:
sqlite3.OperationalError: unable to open database file
```

This blocked Agent 5 from certifying execution/accounting integrity multiple times.

The chosen next direction is:

```text
Move active runtime state to Postgres.
SQLite may remain only for backup/export/offline analysis.
```

---

## 3. Six-Agent Structure Created

### Agent 1 — Core Logic & Control Loop

**Files:**

```text
main.py
start.sh
Dockerfile
docker-compose.yml
core/db.py
runtime DB/startup sections
```

**Responsibilities:**

- Runtime lifecycle
- Docker/startup
- DB path contract
- `main.py` call flow
- Background worker startup
- API/bot process startup
- Prevent unsafe runtime state corruption

### Agent 2 — Configuration & Risk Management

**Files:**

```text
core/config.py
core/portfolio.py
core/risk_engine.py
```

**Responsibilities:**

- Risk thresholds
- Exposure caps
- Drawdown gates
- Portfolio accounting invariants
- Clean reset state
- Percentage-scaled risk behavior

### Agent 3 — AI & Strategy Allocation Layer

**Files:**

```text
ai/autonomous_agent.py
ai/evolutionary_engine.py
ai/rl_allocator.py
main.py allocation handoff only when authorized
```

**Responsibilities:**

- Strategy scoring
- Allocation discipline
- `ALLOCATOR_RESCUE`
- Candidate quality ranking
- Prevent fallback/RAW_MOMENTUM_FALLBACK executable buys
- Ensure only `NORMAL` features become executable buys

### Agent 4 — Data Ingestion & Feature Engineering

**Files:**

```text
data/ingestion.py
feature_store.py
```

**Responsibilities:**

- Real MEXC market data
- Price validation
- Feature buffers
- Technical indicators
- `NORMAL` feature generation
- Feature health logging
- Keep `RAW_MOMENTUM_FALLBACK` diagnostic-only

### Agent 5 — Order Execution

**Files:**

```text
executor.py if present
main.py execution/fill application sections
trade/position DB write paths
```

**Responsibilities:**

- Paper execution
- Buy/sell accounting
- Prevent SELL-only lifecycle
- Prevent duplicate full exits
- Ensure `final_applied_fills=0` has no DB side effects
- Validate trade and position persistence

### Agent 6 — UI Services & Monitoring APIs

**Files:**

```text
services/api.py
dashboard/static/template files if present
```

**Responsibilities:**

- `/status`
- `/trades`
- `/positions`
- `/portfolio/latest`
- Dashboard metrics
- Win-rate calculation
- Anomaly warnings
- API safety when DB tables are missing or corrupted

---

## 4. Agent Status at End of Chat

### Agent 2 — PASS

Agent 2 fixed the stale drawdown/risk gate issue.

Accepted evidence:

```text
py_compile core/config.py        PASS
py_compile core/portfolio.py     PASS
py_compile core/risk_engine.py   PASS
py_compile main.py               PASS
pytest Agent 2 tests             8 passed
/status                          OK
risk_status                      SAFE
equity                           100.0
cash                             100.0
exposure                         0.0
positions                        []
trades                           0
live_trading                     false
```

Agent 2 is frozen unless new risk issues appear.

---

### Agent 4 — PASS

Agent 4 fixed the feature generation issue.

Original failure:

```text
Market symbols: 59
Feature symbols: 0
features_empty
```

Agent 4 found that `FeatureStore.update()` was fragile about input shape. It previously handled raw price maps but not full tick objects like:

```python
{
    "prices": {...},
    "timestamp": ...,
    "source": ...,
    "count": ...
}
```

Accepted evidence:

```text
RAW_INPUT_TEST_PASS
TICK_OBJECT_TEST_PASS
Feature symbols: 59
feature.source = NORMAL
feature.ready = True
ready_features > 0
normal_features > 0
```

Agent 4 remains PASS. `RAW_MOMENTUM_FALLBACK` remains diagnostic only.

---

### Agent 3 — PASS

Agent 3 initially failed because a legacy `main.py` `ALLOCATOR_RESCUE` path bypassed the entry-quality top list.

Failure evidence:

```text
ENTRY QUALITY TOP 10: [('XMR/USDT', ...)]
[ALLOCATOR_RESCUE] selected symbol=HYPE/USDT
```

and later:

```text
ENTRY QUALITY TOP 10: []
[ALLOCATOR_RESCUE] selected symbol=HYPE/USDT
[ALLOCATOR_RESCUE] injected_orders count=1
```

Agent 3 then patched the legacy rescue path so rescue can only inject if:

```text
ENTRY QUALITY TOP 10 is non-empty
selected symbol is in ENTRY QUALITY TOP 10
feature.source == NORMAL
feature.ready == True
symbol_regime is SYMBOL_TREND_UP or SYMBOL_BREAKOUT_UP
signal_strength is positive
confidence is numeric
metadata is attached
```

Accepted PASS evidence:

```text
ENTRY QUALITY TOP 10: includes BSB/USDT
ALLOCATOR_RESCUE selected BSB/USDT
feature_source=NORMAL
ready=True
symbol_regime=SYMBOL_BREAKOUT_UP
entry_reason=evo_allocator_rescue_normal_top_quality
confidence=0.9
signal_strength present
```

Agent 3 is frozen unless allocation regressions appear.

---

### Agent 1 — Reopened, then SQLite Direction Rejected

Agent 1 repaired several runtime issues:

- Restored compiling `main.py`
- Repaired `start.sh`
- Fixed UTF-8 BOM/CRLF issue causing:

```text
exec ./start.sh: exec format error
```

- Standardized SQLite path temporarily to:

```text
/app/data/quant.db
```

- Added/verified DB environment variables
- Produced clean startup DB checks

However, the DB failure kept recurring during Agent 5 runtime validation.

Even after `DB_OK` passed at startup and after short probes, active runtime workers later produced:

```text
unable to open database file
OperationalError('unable to open database file')
```

The final PM decision was not to continue the SQLite repair loop.

---

### Agent 5 — BLOCKED

Agent 5 repeatedly attempted execution/accounting validation but could not certify because runtime DB access failed during the observation window.

Agent 5 confirmed clean starts multiple times:

```text
main.py compile: PASS
initial DB_OK: PASS
/status before observation: PASS
equity=100.0
cash=100.0
exposure=0.0
positions=[]
total_trades=0
buy_count=0
sell_count=0
```

But after 15–20 minutes:

```text
sqlite3.OperationalError: unable to open database file
```

Runtime failures appeared in:

```text
BIG_LOSS_COOLDOWN
PORTFOLIO_RECONCILER
QFOS_AUTO_STALE_RECONCILER
PROFIT_ENGINE
ACTIVE_POSITION_WATCHDOG
EMERGENCY_BASKET_WATCHDOG
```

Agent 5 could not certify:

```text
final_applied_fills=0 creates no trade rows
blocked BUYs do not mutate cash/exposure/positions
no SELL-only lifecycle
no stale paper_position_sync executable positions
protective SELL accounting correctness
duplicate full exit prevention
```

Agent 5 remains blocked until runtime DB architecture is fixed.

---

### Agent 6 — HOLD

Agent 6 was not activated yet because dashboard/API validation should only happen after:

```text
runtime DB is stable
execution/accounting is certified
```

Agent 6 will later validate:

- Dashboard math
- Win rate
- TP/SL counts
- Duplicate/anomaly warnings
- `/status` consistency
- `/trades` consistency

---

## 5. Major Bugs Encountered

### 5.1 Bad `main.py` Regex Patch

A bad replacement corrupted `main.py` into:

```python
def print('[PROFIT_ENGINE] disabled_for_24h_stability_run', flush=True)
```

This caused:

```text
SyntaxError: invalid syntax
```

Recovery was done by testing backup files until one compiled.

Restored good backup:

```text
main.py.bak_disable_profit_engine_20260603_204638
```

---

### 5.2 Duplicate SELL Rows

Dashboard showed impossible counts like:

```text
Buy 3 / Sell 212
```

Repeated sells appeared for the same symbol/quantity/strategy, such as:

```text
EDEN/USDT SELL sideways_green_to_red_exit
NEAR/USDT SELL sideways_green_to_red_exit
```

The issue was traced to profit-engine/direct sell paths bypassing normal execution protections. This motivated the Agent 5 execution/accounting validation, but final certification was blocked by DB instability.

---

### 5.3 SELL-only Rows After Reset

Another run showed SELL-only rows after a supposed clean reset:

```text
buy_count = 0
sell_count = 5
strategy = sideways_hard_exposure_guard
is_exit = false
exit_reason = null
```

Symbols included:

```text
BEAT/USDT
CAKE/USDT
TRIA/USDT
ULTIMA/USDT
ZEC/USDT
```

This remained an Agent 5 issue to certify/fix, but DB instability prevented completion.

---

### 5.4 Feature Pipeline Regression

The bot repeatedly showed:

```text
Feature symbols: 0
features_empty
```

Agent 4 fixed this by making `FeatureStore` robust to both raw price maps and full tick objects.

---

### 5.5 ALLOCATOR_RESCUE Rank Bypass

Legacy rescue in `main.py` selected candidates outside `ENTRY QUALITY TOP 10`.

Agent 3 patched the rescue path so selected rescue symbols must be in the visible entry-quality list and must use `NORMAL` features only.

---

### 5.6 SQLite Runtime Instability

The decisive recurring failure:

```text
DB_OK at startup
DB fails during active runtime
unable to open database file
```

This occurred despite Compose repair, start.sh repair, and path standardization.

Final PM conclusion:

```text
Do not keep trying to patch SQLite.
Move active runtime state to Postgres.
```

---

## 6. Final PM Roadmap

### Stop Current Runtime

Immediate command:

```powershell
cd C:\Users\Administrator\Documents\quant-fund-os
docker compose down
```

### Phase 3B — Runtime State Architecture Stabilization

New joint owner:

```text
Agent 1 + Agent 5
```

Objective:

```text
Replace active runtime SQLite with Postgres.
```

SQLite role after migration:

```text
backup/export/offline analysis only
```

---

## 7. Joint Agent 1 + Agent 5 Postgres Migration Task

### Goal

Move all active bot/API runtime state to Postgres.

### Required Docker Compose Service

```yaml
postgres:
  image: postgres:16
  environment:
    POSTGRES_DB: quant_fund_os
    POSTGRES_USER: qfos
    POSTGRES_PASSWORD: qfos_password
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data
```

### Required Quant DB URL

```text
DATABASE_URL=postgresql+psycopg2://qfos:qfos_password@postgres:5432/quant_fund_os
```

### Required Runtime Rule

Forbidden active runtime calls:

```text
sqlite3.connect(...)
data/quant.db
/app/data/quant.db
quant.db
raw SQLite runtime writes
```

Allowed only in offline/export scripts:

```text
SQLite export
manual backup
offline audit
```

---

## 8. Required Postgres Tables

Postgres must include:

```text
portfolio_snapshots
positions
trades
strategy_scores
symbol_quarantine
profit_engine_state
profit_engine_peaks
db_probe
```

### Required `trades` Columns

```text
id
symbol
side
quantity
expected_price
fill_price
slippage_bps
pnl
strategy
confidence
live
shadow_mode
is_exit
exit_reason
created_at
```

### Required `positions` Columns

```text
symbol
quantity
avg_entry
realized_pnl
unrealized_pnl
last_price
exposure
strategy
updated_at
```

---

## 9. Postgres Migration Acceptance Tests

### Static

```powershell
python -m py_compile .\main.py
python -m py_compile .\services\api.py
python -m py_compile .\core\config.py
python -m py_compile .\core\db.py
```

### Docker

```powershell
docker compose config
docker compose down
docker compose build quant
docker compose up -d --force-recreate
Start-Sleep -Seconds 60
docker compose ps
```

### Postgres DB Check

```powershell
docker compose exec -T postgres psql -U qfos -d quant_fund_os -c "select 1;"
```

### API Check

```powershell
Invoke-RestMethod http://127.0.0.1:8080/status | ConvertTo-Json -Depth 10
```

Expected baseline:

```text
equity=100
cash=100
exposure=0
positions=[]
total_trades=0
buy_count=0
sell_count=0
live_trading=false
bot_state=RUNNING
```

### Runtime Observation

Run 20 minutes.

Must show:

```text
zero unable to open database file
zero OperationalError
zero Traceback
zero Bot loop error
zero SyntaxError
/status remains responsive
Postgres query works after runtime
```

### Agent 5 Accounting Certification

After Postgres migration, Agent 5 must certify:

```text
No DB BUY row unless final_applied_fills includes BUY.
No DB SELL row unless valid open quantity exists.
No duplicate full SELL.
No negative positions.
Blocked BUYs do not mutate cash/exposure/positions.
Protective SELLs have is_exit=true and exit_reason populated.
```

---

## 10. PM Routing at End of Chat

```text
Agent 2 — PASS / frozen
Agent 4 — PASS / frozen
Agent 3 — PASS / frozen
Agent 1 — must join Agent 5 for Postgres migration
Agent 5 — must join Agent 1 for Postgres migration and accounting certification
Agent 6 — HOLD until Postgres + Agent 5 pass
24-hour run — BLOCKED
```

---

## 11. Clear Next Step

Send a joint task to Agent 1 + Agent 5:

```text
Replace runtime SQLite with Postgres.
Do not tune strategy.
Do not alter risk thresholds.
Do not alter feature generation.
Do not alter allocation gates.
Do not alter dashboard math yet.
```

After that task passes:

```text
Agent 6 validates dashboard/API.
Then run a 30–60 minute supervised paper test.
Then run 24-hour test only if 30–60 minute run is clean.
```

---

## 12. PM Final Position

The project is not lost, but the runtime DB layer is not fit for purpose.

The correct move is architectural, not another patch:

```text
Stop patching SQLite.
Migrate runtime state to Postgres.
Then resume execution/accounting validation.
```
