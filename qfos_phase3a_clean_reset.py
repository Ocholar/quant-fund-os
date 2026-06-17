import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()

# Backup old corrupted runtime DB tables into snapshots if possible.
for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {table}_archive_phase3a AS SELECT * FROM {table} WHERE 0")
        cur.execute(f"INSERT INTO {table}_archive_phase3a SELECT * FROM {table}")
    except Exception as e:
        print("ARCHIVE_SKIP", table, e)

for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        cur.execute(f"DELETE FROM {table}")
        print("CLEARED", table)
    except Exception as e:
        print("CLEAR_SKIP", table, e)

# Try to recreate baseline portfolio snapshot only if table shape is simple/compatible.
conn.commit()

print("POST_RESET_COUNTS:")
for table in ["trades", "positions", "portfolio_snapshots"]:
    try:
        print(table, cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception as e:
        print(table, "ERR", e)

conn.close()
