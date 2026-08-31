"""Connection-free API tests for the protected current-user endpoint."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import dependencies
from app.api.dependencies import get_current_user
from app.core.exceptions import AUTHENTICATION_REQUIRED_MESSAGE
from app.core.tokens import (
    ACCESS_TOKEN_ERROR_MESSAGE,
    AccessTokenClaims,
    AccessTokenError,
)
from app.db.session import get_session
from app.main import app
from app.models.user import User


class ControlledRepository:
    """Return a controlled ORM-like user from the real authentication dependency."""

    result: User | None = None

    def __init__(self, _session: Session) -> None:
        pass

    def get_by_id(self, _user_id: object) -> User | None:
        return type(self).result


def make_user() -> User:
    timestamp = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)
    return cast(
        User,
        SimpleNamespace(
            id=uuid4(),
            email="user@example.com",
            password_hash="$argon2id$internal-test-hash",
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


@pytest.fixture
def current_user_session() -> Iterator[tuple[MagicMock, list[str]]]:
    """Track creation and closure of the request-scoped synchronous Session."""

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


def test_current_user_returns_exact_public_fields_through_real_dependency(
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve a Bearer subject and serialize only the existing public allowlist."""

    session, lifecycle = current_user_session
    user = make_user()
    claims = AccessTokenClaims(
        subject=user.id,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    ControlledRepository.result = user
    monkeypatch.setattr(dependencies, "UserRepository", ControlledRepository)
    monkeypatch.setattr(dependencies, "validate_access_token", lambda _token: claims)

    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer controlled.compact.token"},
    )

    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "created_at", "updated_at"}
    assert response.json()["id"] == str(user.id)
    assert response.json()["email"] == "user@example.com"
    assert "password" not in response.text
    assert "password_hash" not in response.text
    assert "access_token" not in response.text
    assert "controlled.compact.token" not in response.text
    assert session.commit.call_count == 0
    assert session.rollback.call_count == 0
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    ("headers", "invalid_token"),
    [
        ({}, False),
        ({"Authorization": "Basic controlled-value"}, False),
        ({"Authorization": "Bearer"}, False),
        ({"Authorization": "Bearer controlled.invalid.token"}, True),
    ],
    ids=("missing", "wrong-scheme", "malformed", "invalid-token"),
)
def test_current_user_authentication_failures_share_one_401(
    headers: dict[str, str],
    invalid_token: bool,
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep invalid authentication outside the endpoint and return one challenge."""

    _session, lifecycle = current_user_session
    if invalid_token:
        monkeypatch.setattr(
            dependencies,
            "validate_access_token",
            MagicMock(side_effect=AccessTokenError(ACCESS_TOKEN_ERROR_MESSAGE)),
        )

    response = client.get("/api/v1/users/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "controlled" not in response.text
    assert lifecycle == ["opened", "closed"]


def test_current_user_route_uses_dependency_injection_and_public_schema(
    client: TestClient,
) -> None:
    """Prove the router consumes identity without performing its own lookup."""

    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = client.get("/api/v1/users/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "created_at", "updated_at"}
    assert "password_hash" not in response.text


def test_current_user_route_is_versioned_and_get_only(client: TestClient) -> None:
    """Expose only the planned versioned GET operation."""

    assert client.get("/users/me").status_code == 404
    assert client.get("/api/users/me").status_code == 404
    assert client.post("/api/v1/users/me").status_code == 405


def test_current_user_openapi_declares_bearer_and_public_allowlist() -> None:
    """Publish one Bearer-protected GET without internal identity material."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/users/me"]["get"]
    public_user = schema["components"]["schemas"]["PublicUser"]
    security_scheme = schema["components"]["securitySchemes"]["BearerAuth"]

    assert set(schema["paths"]["/api/v1/users/me"]) == {"get"}
    assert set(operation["responses"]) == {"200", "401"}
    assert operation["security"] == [{"BearerAuth": []}]
    assert security_scheme == {
        "type": "http",
        "description": "Short-lived access token issued by POST /api/v1/auth/login",
        "scheme": "bearer",
    }
    assert set(public_user["properties"]) == {
        "id",
        "email",
        "created_at",
        "updated_at",
    }
    assert "password_hash" not in str(operation)
    assert "access_token" not in str(operation)
