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
    ROUND(
      (((sell_price / NULLIF(weighted_entry_price, 0)) - 1) * 100)::numeric,
      6
    ) AS price_return_pct,
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
  SELECT
    b.symbol,
    COUNT(*) AS stop_loss_exits,
    ROUND(SUM(s.pnl)::numeric, 10) AS total_sell_row_pnl,
    ROUND(AVG(s.pnl)::numeric, 10) AS avg_sell_row_pnl,
    ROUND(MIN(s.pnl)::numeric, 10) AS worst_sell_row_pnl,
    ROUND(AVG(b.confidence)::numeric, 6) AS avg_entry_confidence,
    STRING_AGG(DISTINCT b.strategy, ' | ') AS entry_strategies,
    MIN(s.created_at) AS first_stop_time,
    MAX(s.created_at) AS last_stop_time
  FROM trades s
  LEFT JOIN LATERAL (
    SELECT b1.*
    FROM trades b1
    WHERE LOWER(b1.side) = 'buy'
      AND b1.symbol = s.symbol
      AND b1.id < s.id
    ORDER BY b1.id DESC
    LIMIT 1
  ) b ON true
  WHERE LOWER(s.side) = 'sell'
    AND s.exit_reason = 'sideways_stop_loss_exit'
  GROUP BY b.symbol
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
