"""Connection-free API tests for the versioned registration endpoint."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth
from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.db.session import get_session
from app.main import app
from app.schemas.user import PublicUser, UserRegistrationRequest


@pytest.fixture
def registration_session() -> Iterator[tuple[MagicMock, list[str]]]:
    """Override the request Session without opening a database connection."""

    session = MagicMock(spec=Session)
    lifecycle: list[str] = []

    def override_session() -> Iterator[Session]:
        lifecycle.append("opened")
        try:
            yield session
        finally:
            lifecycle.append("closed")

    app.dependency_overrides[get_session] = override_session
    try:
        yield session, lifecycle
    finally:
        app.dependency_overrides.pop(get_session, None)


def make_public_user() -> PublicUser:
    """Return the four-field public result expected from the service."""

    timestamp = datetime(2026, 8, 27, 9, 30, tzinfo=UTC)
    return PublicUser(
        id=uuid4(),
        email="user@example.com",
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_registration_returns_201_and_exact_public_fields(
    client: TestClient,
    registration_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass one validated request and the same Session to the service."""

    session, lifecycle = registration_session
    public_user = make_public_user()
    observed: dict[str, object] = {}

    def fake_register_user(
        registration: UserRegistrationRequest,
        received_session: Session,
    ) -> PublicUser:
        observed["registration"] = registration
        observed["session"] = received_session
        return public_user

    monkeypatch.setattr(auth, "register_user", fake_register_user)

    response = client.post(
        "/api/v1/auth/register",
        json={"email": "  User@EXAMPLE.COM ", "password": "valid password value"},
    )

    assert response.status_code == 201
    assert set(response.json()) == {"id", "email", "created_at", "updated_at"}
    assert response.json()["email"] == "user@example.com"
    assert "password" not in response.text
    assert "password_hash" not in response.text
    registration = observed["registration"]
    assert isinstance(registration, UserRegistrationRequest)
    assert registration.email == "user@example.com"
    assert observed["session"] is session
    assert lifecycle == ["opened", "closed"]


def test_registration_validation_failure_does_not_call_service(
    client: TestClient,
    registration_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return 422 at the schema boundary without entering the use case."""

    _session, lifecycle = registration_session
    service = Mock()
    monkeypatch.setattr(auth, "register_user", service)

    response = client.post(
        "/api/v1/auth/register",
        json={"email": "invalid-email", "password": "valid password value"},
    )

    assert response.status_code == 422
    service.assert_not_called()
    assert "valid password value" not in response.text
    assert lifecycle == ["opened", "closed"]


def test_registration_service_error_still_closes_session_dependency(
    client: TestClient,
    registration_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalize the request Session when the service propagates an error."""

    _session, lifecycle = registration_session

    def fail_registration(
        _registration: UserRegistrationRequest,
        _session: Session,
    ) -> PublicUser:
        raise RuntimeError("safe controlled failure")

    monkeypatch.setattr(auth, "register_user", fail_registration)

    with pytest.raises(RuntimeError, match="safe controlled failure"):
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "valid password value"},
        )

    assert lifecycle == ["opened", "closed"]


def test_registration_route_is_only_versioned_and_post_only(
    client: TestClient,
    registration_session: tuple[MagicMock, list[str]],
) -> None:
    """Expose no unversioned route or additional HTTP method."""

    assert client.post("/auth/register", json={}).status_code == 404
    assert client.post("/register", json={}).status_code == 404
    assert client.get("/api/v1/auth/register").status_code == 405


def test_registration_openapi_has_bounded_request_and_response_contracts() -> None:
    """Document POST with request, 201, and 422 without internal fields."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/auth/register"]["post"]
    schemas = schema["components"]["schemas"]

    assert set(schema["paths"]["/api/v1/auth/register"]) == {"post"}
    assert operation["requestBody"]["required"] is True
    assert set(operation["responses"]) == {"201", "409", "422"}
    assert set(schemas["UserRegistrationRequest"]["properties"]) == {
        "email",
        "password",
    }
    assert set(schemas["PublicUser"]["properties"]) == {
        "id",
        "email",
        "created_at",
        "updated_at",
    }
    assert "password_hash" not in str(operation)
    assert "password_hash" not in str(schemas["PublicUser"])


@pytest.mark.parametrize("conflict_path", ["early", "constraint"])
def test_duplicate_email_domain_paths_return_the_same_safe_409(
    conflict_path: str,
    client: TestClient,
    registration_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map either service conflict path to one non-sensitive HTTP response."""

    _session, lifecycle = registration_session

    def duplicate_registration(
        _registration: UserRegistrationRequest,
        _session: Session,
    ) -> PublicUser:
        raise DuplicateEmailError(DUPLICATE_EMAIL_MESSAGE)

    monkeypatch.setattr(auth, "register_user", duplicate_registration)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "  User@EXAMPLE.COM ",
            "password": "valid password value",
        },
    )

    assert conflict_path in {"early", "constraint"}
    assert response.status_code == 409
    assert response.json() == {"detail": DUPLICATE_EMAIL_MESSAGE}
    assert "User@EXAMPLE.COM" not in response.text
    assert "valid password value" not in response.text
    assert "password_hash" not in response.text
    assert "sql" not in response.text.casefold()
    assert lifecycle == ["opened", "closed"]
