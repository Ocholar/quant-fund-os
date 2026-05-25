from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.config import settings

from sqlalchemy import event

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()

def execute(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
