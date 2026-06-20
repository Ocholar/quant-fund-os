-- OPTIONAL SAFETY CLEANUP FOR PM-APPROVED USE ONLY.
-- Do not run this unless PM accepts that the open positions are invalid orphan rows.
--
-- This script archives orphan positions before closing them.
-- It only affects positions where quantity > 0 and no trade row exists for the same symbol.

begin;

create table if not exists ledger_orphan_positions_audit (
  audit_id bigserial primary key,
  archived_at timestamptz default now(),
  reason text not null,
  symbol text,
  quantity numeric,
  avg_entry numeric,
  realized_pnl numeric,
  unrealized_pnl numeric,
  last_price numeric,
  exposure numeric,
  strategy text,
  updated_at timestamptz
);

insert into ledger_orphan_positions_audit (
  reason,
  symbol,
  quantity,
  avg_entry,
  realized_pnl,
  unrealized_pnl,
  last_price,
  exposure,
  strategy,
  updated_at
)
select
  'orphan_open_position_no_trade_lineage_agent5_audit',
  p.symbol,
  p.quantity,
  p.avg_entry,
  p.realized_pnl,
  p.unrealized_pnl,
  p.last_price,
  p.exposure,
  p.strategy,
  p.updated_at
from positions p
where p.quantity > 0
  and not exists (
    select 1
    from trades t
    where t.symbol = p.symbol
  );

update positions p
set
  quantity = 0,
  exposure = 0,
  unrealized_pnl = 0,
  strategy = coalesce(p.strategy, '') || '|orphan_closed_by_agent5_audit',
  updated_at = now()
where p.quantity > 0
  and not exists (
    select 1
    from trades t
    where t.symbol = p.symbol
  );

commit;

-- Verification:
select * from ledger_orphan_positions_audit order by audit_id desc limit 20;
select * from positions where quantity > 0 order by symbol;
select count(*) as trades_count from trades;
