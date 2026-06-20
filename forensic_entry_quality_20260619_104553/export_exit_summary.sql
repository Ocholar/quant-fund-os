\copy (
  select
    coalesce(exit_reason, strategy, 'unknown') as exit_reason,
    count(*) as exits,
    count(*) filter (where pnl > 0) as winners,
    count(*) filter (where pnl < 0) as losers,
    round(avg(pnl)::numeric, 10) as avg_sell_row_pnl,
    round(sum(pnl)::numeric, 10) as total_sell_row_pnl,
    round(min(pnl)::numeric, 10) as worst_sell_row_pnl,
    round(max(pnl)::numeric, 10) as best_sell_row_pnl
  from trades
  where lower(side)='sell'
  group by coalesce(exit_reason, strategy, 'unknown')
  order by total_sell_row_pnl asc
) to '/tmp/qfos_exit_reason_summary.csv' csv header;
