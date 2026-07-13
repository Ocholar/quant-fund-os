from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.config import settings
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
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
