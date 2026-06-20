COPY (
  SELECT
    COALESCE(exit_reason, strategy, 'unknown') AS exit_reason,
    COUNT(*) AS exits,
    COUNT(*) FILTER (WHERE pnl > 0) AS winners,
    COUNT(*) FILTER (WHERE pnl < 0) AS losers,
    ROUND(AVG(pnl)::numeric, 10) AS avg_sell_row_pnl,
    ROUND(SUM(pnl)::numeric, 10) AS total_sell_row_pnl,
    ROUND(MIN(pnl)::numeric, 10) AS worst_sell_row_pnl,
    ROUND(MAX(pnl)::numeric, 10) AS best_sell_row_pnl
  FROM trades
  WHERE LOWER(side) = 'sell'
  GROUP BY COALESCE(exit_reason, strategy, 'unknown')
  ORDER BY total_sell_row_pnl ASC
) TO '/tmp/qfos_exit_reason_summary.csv' WITH CSV HEADER;

COPY (
  WITH buys AS (
    SELECT
      id,
      symbol,
      quantity,
      fill_price,
      strategy,
      confidence,
      created_at,
      COALESCE(
        SUM(quantity) OVER (
          PARTITION BY symbol
          ORDER BY id
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ), 0
      ) AS buy_before,
      SUM(quantity) OVER (
        PARTITION BY symbol
        ORDER BY id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS buy_after
    FROM trades
    WHERE LOWER(side) = 'buy'
  ),
  sells AS (
    SELECT
      id,
      symbol,
      quantity,
      fill_price,
      strategy,
      exit_reason,
      pnl,
      confidence,
      created_at,
      COALESCE(
        SUM(quantity) OVER (
          PARTITION BY symbol
          ORDER BY id
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ), 0
      ) AS sell_before,
      SUM(quantity) OVER (
        PARTITION BY symbol
        ORDER BY id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS sell_after
    FROM trades
    WHERE LOWER(side) = 'sell'
  ),
  matched AS (
    SELECT
      s.id AS sell_id,
      s.symbol,
      s.quantity AS sell_quantity,
      s.fill_price AS sell_price,
      s.strategy AS exit_strategy,
      s.exit_reason,
      s.pnl AS sell_row_pnl,
      s.created_at AS exit_time,
      b.id AS buy_id,
      b.fill_price AS entry_price,
      b.strategy AS entry_strategy,
      b.confidence AS entry_confidence,
      b.created_at AS entry_time,
      GREATEST(
        0::double precision,
        LEAST(b.buy_after, s.sell_after) - GREATEST(b.buy_before, s.sell_before)
      ) AS matched_qty
    FROM sells s
    JOIN buys b
      ON b.symbol = s.symbol
     AND b.buy_after > s.sell_before
     AND b.buy_before < s.sell_after
  ),
  rollup AS (
    SELECT
      sell_id,
      symbol,
      sell_quantity,
      sell_price,
      exit_strategy,
      exit_reason,
      sell_row_pnl,
      exit_time,
      MIN(entry_time) AS first_entry_time,
      STRING_AGG(DISTINCT COALESCE(entry_strategy, 'unknown'), ' | ') AS entry_strategies,
      ROUND(AVG(entry_confidence)::numeric, 6) AS avg_entry_confidence,
      SUM(matched_qty) AS matched_quantity,
      SUM(matched_qty * entry_price) / NULLIF(SUM(matched_qty), 0) AS weighted_entry_price
    FROM matched
    WHERE matched_qty > 0
    GROUP BY
      sell_id, symbol, sell_quantity, sell_price,
      exit_strategy, exit_reason, sell_row_pnl, exit_time
  )
  SELECT
    sell_id,
    symbol,
    ROUND(sell_quantity::numeric, 12) AS quantity,
    ROUND(weighted_entry_price::numeric, 12) AS weighted_entry_price,
    ROUND(sell_price::numeric, 12) AS sell_price,
    ROUND(((sell_price / NULLIF(weighted_entry_price, 0)) - 1) * 100, 6) AS price_return_pct,
    ROUND(sell_row_pnl::numeric, 12) AS recorded_sell_row_pnl,
    entry_strategies,
    avg_entry_confidence,
    first_entry_time,
    exit_time,
    ROUND(EXTRACT(EPOCH FROM (exit_time - first_entry_time))::numeric, 2) AS holding_seconds,
    exit_reason
  FROM rollup
  WHERE exit_reason = 'sideways_stop_loss_exit'
  ORDER BY price_return_pct ASC, exit_time ASC
) TO '/tmp/qfos_sideways_stop_loss_fifo.csv' WITH CSV HEADER;

COPY (
  WITH stop_rows AS (
    SELECT
      symbol,
      pnl,
      quantity,
      fill_price,
      created_at
    FROM trades
    WHERE LOWER(side) = 'sell'
      AND exit_reason = 'sideways_stop_loss_exit'
  )
  SELECT
    symbol,
    COUNT(*) AS stop_loss_exits,
    ROUND(SUM(pnl)::numeric, 10) AS total_sell_row_pnl,
    ROUND(AVG(pnl)::numeric, 10) AS avg_sell_row_pnl,
    ROUND(MIN(pnl)::numeric, 10) AS worst_sell_row_pnl,
    MIN(created_at) AS first_stop_time,
    MAX(created_at) AS last_stop_time
  FROM stop_rows
  GROUP BY symbol
  ORDER BY total_sell_row_pnl ASC
) TO '/tmp/qfos_stop_loss_by_symbol.csv' WITH CSV HEADER;

COPY (
  SELECT
    id,
    created_at,
    symbol,
    side,
    quantity,
    fill_price,
    strategy,
    confidence,
    exit_reason,
    pnl
  FROM trades
  ORDER BY id
) TO '/tmp/qfos_all_trades_forensic.csv' WITH CSV HEADER;
