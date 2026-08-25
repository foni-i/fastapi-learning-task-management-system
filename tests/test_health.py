"""Connection-free tests for liveness and database readiness contracts."""

from collections.abc import Iterator
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.api import health as health_api
from app.api.health import database_is_ready
from app.core.config import get_settings
from app.db.probe import check_database_connection
from app.db.session import get_engine, get_session_factory
from app.main import app

LEAKED_DATABASE_DETAIL = (
    "postgresql+psycopg://private-user:private-password@database.example/stms"
)


@pytest.fixture(autouse=True)
def isolate_readiness_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep ordinary health tests independent of environment and PostgreSQL."""

    monkeypatch.delenv("STMS_DATABASE_URL", raising=False)
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()
    app.dependency_overrides.clear()

    yield

    cached_engine = get_engine() if get_engine.cache_info().currsize else None
    app.dependency_overrides.clear()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()
    if cached_engine is not None:
        cached_engine.dispose()


def test_liveness_endpoint_returns_stable_response_from_application(
    client: TestClient,
) -> None:
    """The real application serves the stable liveness response with HTTP 200."""

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_endpoint_rejects_unsupported_method(client: TestClient) -> None:
    """The liveness contract only supports GET requests."""

    response = client.post("/health/live")

    assert response.status_code == 405


def test_readiness_returns_service_unavailable_without_database_url(
    client: TestClient,
) -> None:
    """Missing configuration is a stable 503 rather than an application error."""

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_returns_ok_for_controlled_success(client: TestClient) -> None:
    """The route maps a successful controlled probe to the public 200 contract."""

    app.dependency_overrides[database_is_ready] = lambda: True

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "database_failure",
    [
        OperationalError(None, None, RuntimeError(LEAKED_DATABASE_DETAIL)),
        ProgrammingError("SELECT 1", None, RuntimeError(LEAKED_DATABASE_DETAIL)),
    ],
    ids=["connection-failure", "query-failure"],
)
def test_expected_database_failure_returns_safe_unavailable_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    database_failure: SQLAlchemyError,
) -> None:
    """Connection/query failures disclose neither exceptions nor credentials."""

    def fail_probe() -> None:
        raise database_failure

    monkeypatch.setattr(health_api, "check_database_connection", fail_probe)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert LEAKED_DATABASE_DETAIL not in response.text
    assert "private-password" not in response.text
    assert LEAKED_DATABASE_DETAIL not in caplog.text
    assert "private-password" not in caplog.text


def test_liveness_does_not_invoke_database_probe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness stays process-only even when the database probe would fail."""

    probe = Mock(side_effect=SQLAlchemyError("database unavailable"))
    monkeypatch.setattr(health_api, "check_database_connection", probe)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    probe.assert_not_called()


def test_database_probe_executes_select_one_and_closes_connection() -> None:
    """The synchronous probe returns its Connection after a successful query."""

    engine = Mock(spec=Engine)
    connection_context = MagicMock()
    connection = Mock()
    engine.connect.return_value = connection_context
    connection_context.__enter__.return_value = connection
    connection.execute.return_value.scalar_one.return_value = 1

    check_database_connection(engine)

    statement = connection.execute.call_args.args[0]
    assert str(statement) == "SELECT 1"
    connection.execute.return_value.scalar_one.assert_called_once_with()
    connection_context.__exit__.assert_called_once()


def test_database_probe_closes_connection_when_query_fails() -> None:
    """The Connection context exits even when PostgreSQL rejects the query."""

    engine = Mock(spec=Engine)
    connection_context = MagicMock()
    connection = Mock()
    engine.connect.return_value = connection_context
    connection_context.__enter__.return_value = connection
    connection.execute.side_effect = SQLAlchemyError("query failed")

    with pytest.raises(SQLAlchemyError, match="query failed"):
        check_database_connection(engine)

    connection_context.__exit__.assert_called_once()


def test_unknown_versioned_path_returns_not_found(client: TestClient) -> None:
    """The mounted v1 boundary exposes no product endpoint yet."""

    response = client.get("/api/v1/not-yet-implemented")

    assert response.status_code == 404


def test_liveness_endpoint_is_documented_in_openapi(client: TestClient) -> None:
    """OpenAPI exposes the operation and its explicit response schema."""

    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    operation = openapi["paths"]["/health/live"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert operation["summary"] == "Liveness check"
    assert operation["tags"] == ["health"]
    assert response_schema == {"$ref": "#/components/schemas/LivenessResponse"}
    assert openapi["components"]["schemas"]["LivenessResponse"]["required"] == [
        "status"
    ]


def test_readiness_success_and_failure_are_documented_in_openapi(
    client: TestClient,
) -> None:
    """OpenAPI publishes distinct stable schemas for readiness 200 and 503."""

    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    operation = openapi["paths"]["/health/ready"]["get"]
    success_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    unavailable_schema = operation["responses"]["503"]["content"]["application/json"][
        "schema"
    ]

    assert operation["summary"] == "Readiness check"
    assert operation["tags"] == ["health"]
    assert success_schema == {"$ref": "#/components/schemas/ReadinessResponse"}
    assert unavailable_schema == {
        "$ref": "#/components/schemas/ReadinessUnavailableResponse"
    }
    assert openapi["components"]["schemas"]["ReadinessResponse"]["required"] == [
        "status"
    ]
    assert openapi["components"]["schemas"]["ReadinessUnavailableResponse"][
        "required"
    ] == ["status"]
