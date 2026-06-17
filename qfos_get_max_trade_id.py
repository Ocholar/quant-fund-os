import sqlite3

conn = sqlite3.connect("/app/data/quant.db")
cur = conn.cursor()
row = cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()
print(row[0])
