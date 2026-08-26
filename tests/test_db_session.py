"""Unit tests for lazy synchronous SQLAlchemy infrastructure."""

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import session as db_session
from app.db.base import Base
from app.db.session import (
    DATABASE_CONNECT_TIMEOUT_SECONDS,
    DatabaseConfigurationError,
    get_engine,
    get_session_factory,
)
from app.main import create_app

DATABASE_PASSWORD = "test-only-password"
VALID_DATABASE_URL = (
    f"postgresql+psycopg://test_user:{DATABASE_PASSWORD}@127.0.0.1:5432/test_database"
)


def fail_database_connection(*args: object, **kwargs: object) -> None:
    """Fail a test immediately if SQLAlchemy attempts a real DBAPI connection."""

    raise AssertionError("Task 2.2 tests must not connect to PostgreSQL")


@pytest.fixture(autouse=True)
def isolate_database_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Isolate settings/factory caches and forbid real PostgreSQL connections."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STMS_DATABASE_URL", raising=False)
    monkeypatch.setattr(psycopg, "connect", fail_database_connection)
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()

    yield

    cached_engine = get_engine() if get_engine.cache_info().currsize else None
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()
    if cached_engine is not None:
        cached_engine.dispose()


def configure_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the test-only PostgreSQL URL for the next factory request."""

    monkeypatch.setenv("STMS_DATABASE_URL", VALID_DATABASE_URL)
    get_settings.cache_clear()


def test_base_exposes_shared_metadata_without_import_side_effects() -> None:
    """The SQLAlchemy 2 base remains usable without creating a connection."""

    assert Base.metadata is not None


def test_valid_configuration_creates_lazy_synchronous_psycopg_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine construction is synchronous and does not open a DBAPI connection."""

    configure_database(monkeypatch)

    engine = get_engine()

    assert isinstance(engine, Engine)
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.dialect.is_async is False


def test_engine_configures_a_bounded_connection_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine construction passes a short Psycopg connection timeout."""

    configure_database(monkeypatch)
    engine = Mock(spec=Engine)
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(db_session, "create_engine", create_engine)

    assert get_engine() is engine
    create_engine.assert_called_once_with(
        VALID_DATABASE_URL,
        connect_args={"connect_timeout": DATABASE_CONNECT_TIMEOUT_SECONDS},
    )


def test_session_factory_is_bound_to_configured_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synchronous Session factory reuses the configured lazy Engine."""

    configure_database(monkeypatch)

    engine = get_engine()
    session_factory = get_session_factory()
    session = session_factory()

    try:
        assert issubclass(session_factory.class_, Session)
        assert isinstance(session, Session)
        assert session_factory.kw["bind"] is engine
        assert session.get_bind() is engine
    finally:
        session.close()


def test_session_lifecycle_closes_without_hidden_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifecycle helper closes its one Session even when caller code fails."""

    tracked_session = Mock(spec=Session)
    tracked_factory = Mock(return_value=tracked_session)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: tracked_factory)

    session_iterator = db_session.get_session()
    assert next(session_iterator) is tracked_session

    with pytest.raises(RuntimeError, match="test failure"):
        session_iterator.throw(RuntimeError("test failure"))

    tracked_factory.assert_called_once_with()
    tracked_session.close.assert_called_once_with()
    tracked_session.commit.assert_not_called()


@pytest.mark.parametrize("factory", [get_engine, get_session_factory])
def test_database_factory_requires_configured_url(
    factory: Callable[[], object],
) -> None:
    """Database objects fail explicitly only when requested without configuration."""

    with pytest.raises(
        DatabaseConfigurationError,
        match="STMS_DATABASE_URL is not configured",
    ):
        factory()


def test_application_and_liveness_work_without_database_url() -> None:
    """Database factories remain outside the application import/liveness path."""

    application = create_app()

    with TestClient(application) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_engine_representations_hide_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safe Engine and URL representations never reveal configured credentials."""

    configure_database(monkeypatch)

    engine = get_engine()

    assert DATABASE_PASSWORD not in repr(engine)
    assert DATABASE_PASSWORD not in str(engine.url)
    assert "***" in str(engine.url)
