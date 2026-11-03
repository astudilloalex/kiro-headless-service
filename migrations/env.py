import json
import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

from kiro_headless_service.infrastructure.tables import Base

data = (
    json.loads(Path(os.environ["KHS_CONFIG"]).read_text()) if os.environ.get("KHS_CONFIG") else {}
)
url_file = os.environ.get("KHS_DATABASE_URL_FILE", data.get("database_url_file"))
url = os.environ.get(
    "KHS_DATABASE_URL",
    data.get(
        "database_url",
        "postgresql+psycopg://kiro@localhost:5432/kiro",
    ),
)
if url_file:
    url = Path(url_file).read_text().strip()
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=pool.NullPool, hide_parameters=True).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
