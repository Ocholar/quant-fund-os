\copy (
with stop_rows as (
  select
    symbol,
    pnl,
    quantity,
    fill_price,
    created_at
  from trades
  where lower(side)='sell'
    and exit_reason='sideways_stop_loss_exit'
)
select
  symbol,
  count(*) as stop_loss_exits,
  round(sum(pnl)::numeric, 10) as total_sell_row_pnl,
  round(avg(pnl)::numeric, 10) as avg_sell_row_pnl,
  round(min(pnl)::numeric, 10) as worst_sell_row_pnl,
  min(created_at) as first_stop_time,
  max(created_at) as last_stop_time
from stop_rows
group by symbol
order by total_sell_row_pnl asc
) to '/tmp/qfos_stop_loss_by_symbol.csv' csv header;
