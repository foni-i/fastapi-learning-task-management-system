"""Real PostgreSQL verification for the application readiness endpoint."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import URL, Engine

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def readiness_engine(
    migration_test_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    """Bind readiness only to the validated dedicated Compose test service."""

    target_url = migration_test_database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("STMS_DATABASE_URL", target_url)
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()
    engine = get_engine()

    try:
        yield engine
    finally:
        get_session_factory.cache_clear()
        get_engine.cache_clear()
        get_settings.cache_clear()
        engine.dispose()


def test_readiness_executes_select_one_against_real_postgres_test(
    readiness_engine: Engine,
) -> None:
    """The real application returns 200 only after PostgreSQL executes SELECT 1."""

    statements: list[str] = []

    def record_statement(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(readiness_engine, "before_cursor_execute", record_statement)
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        event.remove(readiness_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert [statement.strip() for statement in statements] == ["SELECT 1"]
