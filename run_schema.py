from sqlalchemy import text
from core.db import engine
schema = open('schema.sql').read()
with engine.begin() as conn:
    for statement in schema.split(';'):
        if statement.strip():
            conn.execute(text(statement))
print("Schema updated successfully.")
