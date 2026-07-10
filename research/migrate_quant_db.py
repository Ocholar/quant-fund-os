import sqlite3

def add_columns_if_missing(db_path="quant.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in cur.fetchall()]
    
    needed = [
        "regime TEXT",
        "mfe REAL",
        "mae REAL",
        "peak_price REAL",
        "trough_price REAL",
        "experiment_id TEXT",
        "software_version TEXT",
        "configuration_hash TEXT",
        "trade_uuid TEXT",
        "source TEXT"
    ]
    
    for col_def in needed:
        col_name = col_def.split()[0]
        if col_name not in columns:
            try:
                print(f"Adding {col_name}...")
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col_def}")
            except Exception as e:
                print(e)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_columns_if_missing()
