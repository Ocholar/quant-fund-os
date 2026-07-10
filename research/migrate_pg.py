import sqlalchemy
from sqlalchemy import text

db_url = "postgresql+psycopg2://qfos:qfos_password@localhost:5432/quant_fund_os"
engine = sqlalchemy.create_engine(db_url)

columns_to_add = {
    "regime": "VARCHAR(255)",
    "experiment_id": "VARCHAR(255)",
    "software_version": "VARCHAR(255)",
    "configuration_hash": "VARCHAR(255)",
    "trade_uuid": "VARCHAR(255)",
    "mfe": "FLOAT",
    "mae": "FLOAT",
    "peak_price": "FLOAT",
    "trough_price": "FLOAT",
    "source": "VARCHAR(255)",
}

with engine.begin() as conn:
    for col, dtype in columns_to_add.items():
        try:
            conn.execute(text(f"ALTER TABLE trades ADD COLUMN {col} {dtype}"))
            print(f"Added column {col}")
        except Exception as e:
            print(f"Error adding {col}: {e}")
