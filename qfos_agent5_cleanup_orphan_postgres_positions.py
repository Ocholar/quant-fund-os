import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

DEFAULT_DSN = "postgresql://qfos:qfos@localhost:5432/quant_fund_os"

SQL_CREATE_ARCHIVE = """
CREATE TABLE IF NOT EXISTS orphan_position_archive (
    id SERIAL PRIMARY KEY,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT,
    quantity DOUBLE PRECISION,
    avg_entry DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    last_price DOUBLE PRECISION,
    exposure DOUBLE PRECISION,
    strategy TEXT,
    reason TEXT,
    source_table TEXT
);
"""

SQL_FIND_ORPHANS = """
SELECT
    p.symbol,
    p.quantity,
    p.avg_entry,
    p.realized_pnl,
    p.unrealized_pnl,
    p.last_price,
    p.exposure,
    p.strategy,
    p.updated_at
FROM positions p
WHERE p.quantity > 0
  AND lower(coalesce(p.strategy,'')) = 'paper_position_sync'
  AND NOT EXISTS (
      SELECT 1
      FROM trades t
      WHERE t.symbol = p.symbol
  )
ORDER BY p.symbol;
"""

SQL_ARCHIVE = """
INSERT INTO orphan_position_archive (
    symbol,
    quantity,
    avg_entry,
    realized_pnl,
    unrealized_pnl,
    last_price,
    exposure,
    strategy,
    reason,
    source_table
)
VALUES (
    %(symbol)s,
    %(quantity)s,
    %(avg_entry)s,
    %(realized_pnl)s,
    %(unrealized_pnl)s,
    %(last_price)s,
    %(exposure)s,
    %(strategy)s,
    'ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE',
    'positions'
);
"""

SQL_ZERO = """
UPDATE positions
SET
    quantity = 0,
    exposure = 0,
    unrealized_pnl = 0,
    strategy = CASE
        WHEN strategy IS NULL OR strategy = '' THEN 'orphan_closed_by_agent5_guard'
        ELSE strategy || '|orphan_closed_by_agent5_guard'
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE symbol = %(symbol)s
  AND quantity > 0
  AND lower(coalesce(strategy,'')) LIKE 'paper_position_sync%%'
  AND NOT EXISTS (
      SELECT 1
      FROM trades t
      WHERE t.symbol = positions.symbol
  );
"""

SQL_INSERT_BASELINE = """
INSERT INTO portfolio_snapshots (
    equity,
    cash,
    exposure,
    drawdown,
    regime
)
VALUES (
    100.0,
    100.0,
    0.0,
    0.0,
    'SIDEWAYS'
);
"""

SQL_VERIFY_COUNTS = """
SELECT
    (SELECT COUNT(*) FROM trades) AS trades,
    (SELECT COUNT(*) FROM positions WHERE quantity > 0) AS open_positions,
    (SELECT COALESCE(SUM(exposure), 0) FROM positions WHERE quantity > 0) AS open_exposure,
    (SELECT COUNT(*) FROM orphan_position_archive WHERE reason='ORPHAN_OPEN_POSITION_NO_TRADE_LINEAGE') AS archived_orphans;
"""

SQL_VERIFY_OPEN = """
SELECT symbol, quantity, exposure, strategy
FROM positions
WHERE quantity > 0
ORDER BY symbol;
"""

SQL_VERIFY_SNAP = """
SELECT id, equity, cash, exposure, drawdown, regime, created_at
FROM portfolio_snapshots
ORDER BY id DESC
LIMIT 5;
"""


def connect():
    dsn = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or DEFAULT_DSN
    )
    print(f"[AGENT5_CLEANUP] connecting_to={dsn.split('@')[-1] if '@' in dsn else dsn}")
    return psycopg2.connect(dsn)


def main():
    conn = None
    try:
        conn = connect()
        conn.autocommit = False

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            print("[AGENT5_CLEANUP] begin_transaction")
            cur.execute(SQL_CREATE_ARCHIVE)

            cur.execute(SQL_FIND_ORPHANS)
            orphans = cur.fetchall()

            print(f"[AGENT5_CLEANUP] orphan_candidates={len(orphans)}")
            for row in orphans:
                print(
                    "[AGENT5_CLEANUP] candidate "
                    f"symbol={row['symbol']} qty={row['quantity']} "
                    f"avg_entry={row['avg_entry']} exposure={row['exposure']} "
                    f"strategy={row['strategy']}"
                )

            if not orphans:
                print("[AGENT5_CLEANUP] no orphan paper_position_sync positions found")
            else:
                for row in orphans:
                    cur.execute(SQL_ARCHIVE, row)
                    cur.execute(SQL_ZERO, row)

            cur.execute(SQL_INSERT_BASELINE)

            cur.execute(SQL_VERIFY_COUNTS)
            counts = cur.fetchone()
            print("[AGENT5_CLEANUP] verification_counts", dict(counts))

            cur.execute(SQL_VERIFY_OPEN)
            open_rows = cur.fetchall()
            print(f"[AGENT5_CLEANUP] open_positions_after={len(open_rows)}")
            for row in open_rows:
                print("[AGENT5_CLEANUP] remaining_open", dict(row))

            cur.execute(SQL_VERIFY_SNAP)
            snaps = cur.fetchall()
            print("[AGENT5_CLEANUP] latest_snapshots")
            for row in snaps:
                print(dict(row))

        conn.commit()
        print("[AGENT5_CLEANUP] COMMIT_OK")

    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[AGENT5_CLEANUP] ROLLBACK error={repr(e)}")
        raise

    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
