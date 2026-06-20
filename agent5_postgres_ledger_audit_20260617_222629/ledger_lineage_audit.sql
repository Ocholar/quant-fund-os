-- Agent 5 Postgres ledger lineage audit.
-- Non-destructive.

\echo 'OPEN_POSITION_COUNTS'
select
  count(*) filter (where quantity > 0) as open_position_count,
  count(*) as total_position_rows
from positions;

\echo 'TRADE_COUNTS'
select
  count(*) as total_trades,
  count(*) filter (where lower(side)='buy') as buy_count,
  count(*) filter (where lower(side)='sell') as sell_count
from trades;

\echo 'OPEN_POSITIONS_WITH_TRADE_LINEAGE'
with trade_lifecycle as (
  select
    symbol,
    coalesce(sum(case when lower(side)='buy' then quantity else 0 end),0) as buy_qty,
    coalesce(sum(case when lower(side)='sell' then quantity else 0 end),0) as sell_qty,
    coalesce(sum(case
      when lower(side)='buy' then quantity
      when lower(side)='sell' then -quantity
      else 0
    end),0) as net_trade_qty,
    count(*) as trade_rows,
    min(created_at) as first_trade_at,
    max(created_at) as last_trade_at
  from trades
  group by symbol
)
select
  p.*,
  coalesce(t.trade_rows,0) as trade_rows,
  coalesce(t.buy_qty,0) as buy_qty,
  coalesce(t.sell_qty,0) as sell_qty,
  coalesce(t.net_trade_qty,0) as net_trade_qty,
  t.first_trade_at,
  t.last_trade_at,
  case
    when p.quantity <= 0 then 'CLOSED_OR_ZERO'
    when coalesce(t.trade_rows,0)=0 then 'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE'
    when coalesce(t.net_trade_qty,0) <= 0 then 'INVALID_OPEN_POSITION_NET_TRADE_QTY_NOT_POSITIVE'
    when abs(p.quantity - coalesce(t.net_trade_qty,0)) > 0.00000001 then 'POSITION_QTY_MISMATCHES_TRADE_NET_QTY'
    else 'VALID_TRACEABLE_POSITION'
  end as lineage_status
from positions p
left join trade_lifecycle t on t.symbol = p.symbol
where p.quantity > 0
order by p.symbol;

\echo 'ORPHAN_OPEN_POSITIONS'
with trade_lifecycle as (
  select symbol, count(*) as trade_rows
  from trades
  group by symbol
)
select p.*
from positions p
left join trade_lifecycle t on t.symbol=p.symbol
where p.quantity > 0
  and coalesce(t.trade_rows,0)=0
order by p.symbol;

\echo 'SELLS_WITHOUT_PRIOR_BUY_LINEAGE'
select s.*
from trades s
where lower(s.side)='sell'
  and coalesce((
    select sum(b.quantity)
    from trades b
    where b.symbol=s.symbol
      and lower(b.side)='buy'
      and b.id < s.id
  ),0) <= 0.00000001
order by s.id desc
limit 50;

\echo 'PROTECTIVE_SELLS_BAD_EXIT_ACCOUNTING'
select *
from trades
where lower(side)='sell'
  and (
    coalesce(is_exit,false)=false
    or exit_reason is null
    or trim(exit_reason)=''
  )
order by id desc
limit 50;

\echo 'NEGATIVE_POSITIONS'
select *
from positions
where quantity < -0.00000001
order by symbol;
