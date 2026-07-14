from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.config import settings
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Pool sizing: trading loop + API reads share this pool.
    # 15 connections is sufficient; overflow gives burst headroom.
    pool_size=15,
    max_overflow=10,
    pool_timeout=10,
    # Recycle connections after 2 minutes so idle-in-transaction leaks
    # are cleaned up automatically without manual pg_terminate_backend.
    pool_recycle=120,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {
        "keepalives": 1,
        "keepalives_idle": 15,
        "keepalives_interval": 5,
        "keepalives_count": 3,
        # Kill any individual statement stuck longer than 30 s.
        # This prevents one stalled query from blocking the entire pool.
        "options": "-c statement_timeout=30000",
    }
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
def execute(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
