"""Minimal synchronous PostgreSQL readiness probe."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.session import get_engine


def check_database_connection(engine: Engine | None = None) -> None:
    """Execute a minimal query and always return the Connection to its pool."""

    database_engine = engine if engine is not None else get_engine()
    with database_engine.connect() as connection:
        connection.execute(text("SELECT 1")).scalar_one()
