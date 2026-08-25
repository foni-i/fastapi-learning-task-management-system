"""Lazy synchronous SQLAlchemy engine and Session factories."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


class DatabaseConfigurationError(RuntimeError):
    """Raised when database infrastructure is requested without a URL."""


@lru_cache
def get_engine() -> Engine:
    """Create the process-wide synchronous Engine without opening a connection."""

    database_url = get_settings().database_url
    if database_url is None:
        raise DatabaseConfigurationError("STMS_DATABASE_URL is not configured")

    return create_engine(database_url.get_secret_value())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the synchronous Session factory bound to the configured Engine."""

    return sessionmaker(bind=get_engine(), class_=Session)


def get_session() -> Generator[Session]:
    """Yield one short-lived Session and always close it without committing."""

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
