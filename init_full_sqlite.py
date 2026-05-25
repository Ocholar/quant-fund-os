import sqlite3
from core.config import settings

def init_db():
    print(f"Initializing authoritative schema for {settings.database_url}")
    # Extract the file path
    db_path = settings.database_url.replace("sqlite:///", "")

    with sqlite3.connect(db_path) as conn:
        with open("schema.sql") as f:
            conn.executescript(f.read())
        
        # Run safe migrations
        try:
            conn.execute("ALTER TABLE positions ADD COLUMN strategy TEXT;")
            print("Migrated positions: Added strategy column")
        except sqlite3.OperationalError:
            pass # Column likely exists
        
        conn.commit()

if __name__ == "__main__":
    init_db()
