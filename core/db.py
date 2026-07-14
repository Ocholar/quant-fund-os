from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.config import settings
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Pool increased from defaults (5/10) to give /status headroom while the
    # trading loop holds connections.  pool_timeout shortened so API requests
    # fail fast (HTTP 503) rather than hanging for 30 s before a 500.
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {
        "keepalives": 1,
        "keepalives_idle": 15,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    }
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
def execute(sql: str, params: dict | None = None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
