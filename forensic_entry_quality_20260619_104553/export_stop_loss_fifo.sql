\copy (
with buys as (
  select
    id,
    symbol,
    quantity,
    fill_price,
    strategy,
    created_at,
    coalesce(
      sum(quantity) over (
        partition by symbol
        order by id
        rows between unbounded preceding and 1 preceding
      ), 0
    ) as buy_before,
    sum(quantity) over (
      partition by symbol
      order by id
      rows between unbounded preceding and current row
    ) as buy_after
  from trades
  where lower(side) = 'buy'
),
sells as (
  select
    id,
    symbol,
    quantity,
    fill_price,
    strategy,
    exit_reason,
    pnl,
    created_at,
    coalesce(
      sum(quantity) over (
        partition by symbol
        order by id
        rows between unbounded preceding and 1 preceding
      ), 0
    ) as sell_before,
    sum(quantity) over (
      partition by symbol
      order by id
      rows between unbounded preceding and current row
    ) as sell_after
  from trades
  where lower(side) = 'sell'
),
matched as (
  select
    s.id as sell_id,
    s.symbol,
    s.quantity as sell_quantity,
    s.fill_price as sell_price,
    s.strategy as exit_strategy,
    s.exit_reason,
    s.pnl as sell_row_pnl,
    s.created_at as exit_time,
    b.id as buy_id,
    b.fill_price as entry_price,
    b.strategy as entry_strategy,
    b.created_at as entry_time,
    greatest(
      0::double precision,
      least(b.buy_after, s.sell_after) - greatest(b.buy_before, s.sell_before)
    ) as matched_qty
  from sells s
  join buys b
    on b.symbol = s.symbol
   and b.buy_after > s.sell_before
   and b.buy_before < s.sell_after
),
rollup as (
  select
    sell_id,
    symbol,
    sell_quantity,
    sell_price,
    exit_strategy,
    exit_reason,
    sell_row_pnl,
    exit_time,
    min(entry_time) as first_entry_time,
    string_agg(distinct coalesce(entry_strategy, 'unknown'), ' | ') as entry_strategies,
    sum(matched_qty) as matched_quantity,
    sum(matched_qty * entry_price) / nullif(sum(matched_qty), 0) as weighted_entry_price
  from matched
  where matched_qty > 0
  group by
    sell_id, symbol, sell_quantity, sell_price,
    exit_strategy, exit_reason, sell_row_pnl, exit_time
)
select
  sell_id,
  symbol,
  round(sell_quantity::numeric, 12) as quantity,
  round(weighted_entry_price::numeric, 12) as weighted_entry_price,
  round(sell_price::numeric, 12) as sell_price,
  round(
    ((sell_price / nullif(weighted_entry_price, 0)) - 1) * 100,
    6
  ) as price_return_pct,
  round(sell_row_pnl::numeric, 12) as recorded_sell_row_pnl,
  entry_strategies,
  first_entry_time,
  exit_time,
  round(extract(epoch from (exit_time - first_entry_time))::numeric, 2) as holding_seconds,
  exit_reason
from rollup
where exit_reason = 'sideways_stop_loss_exit'
order by price_return_pct asc, exit_time asc
) to '/tmp/qfos_sideways_stop_loss_fifo.csv' csv header;
