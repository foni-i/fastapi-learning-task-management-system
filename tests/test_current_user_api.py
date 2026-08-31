"""Connection-free API tests for the protected current-user endpoint."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api import dependencies
from app.api.dependencies import get_current_user
from app.api.v1.endpoints import users
from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    DUPLICATE_EMAIL_MESSAGE,
    DuplicateEmailError,
)
from app.core.tokens import (
    ACCESS_TOKEN_ERROR_MESSAGE,
    AccessTokenClaims,
    AccessTokenError,
)
from app.db.session import get_session
from app.main import app
from app.models.user import User
from app.schemas.user import CurrentUserEmailUpdate, PublicUser


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


@pytest.mark.parametrize(
    ("raw_email", "expected"),
    [
        (" NEW@EXAMPLE.COM ", "new@example.com"),
        ("User@例子.测试", "user@例子.测试"),
        ("User@xn--fsqu00a.xn--0zwm56d", "user@例子.测试"),
    ],
)
def test_current_user_email_update_stores_one_canonical_field(
    raw_email: str,
    expected: str,
) -> None:
    """Reuse the shared canonical email boundary for profile updates."""

    update = CurrentUserEmailUpdate.model_validate({"email": raw_email})

    assert update.model_dump() == {"email": expected}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": ""},
        {"email": "   "},
        {"email": "invalid-email"},
        {"email": "user@example.com", "password": "not-allowed"},
        {"email": "user@example.com", "user_id": str(uuid4())},
        {"email": "user@example.com", "name": "not-allowed"},
    ],
    ids=(
        "missing",
        "empty",
        "blank",
        "invalid",
        "password",
        "user-id",
        "profile-field",
    ),
)
def test_current_user_email_update_rejects_invalid_or_extra_fields(
    payload: dict[str, object],
) -> None:
    """Keep the PATCH allowlist restricted to one valid email field."""

    with pytest.raises(ValidationError):
        CurrentUserEmailUpdate.model_validate(payload)


def test_current_user_email_update_rejects_overlong_email() -> None:
    """Preserve the canonical 254-character storage boundary."""

    domain = ".".join(("b" * 63, "c" * 63, "d" * 62))

    with pytest.raises(ValidationError):
        CurrentUserEmailUpdate.model_validate({"email": f"{'a' * 64}@{domain}"})


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


def test_current_user_email_patch_delegates_normalized_input_and_same_session(
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep HTTP wiring thin and return the exact public allowlist."""

    session, lifecycle = current_user_session
    user = make_user()
    observed: dict[str, object] = {}

    def fake_update(
        update: CurrentUserEmailUpdate,
        received_user: User,
        received_session: Session,
    ) -> PublicUser:
        observed["update"] = update
        observed["user"] = received_user
        observed["session"] = received_session
        user.email = update.email
        return PublicUser.model_validate(user)

    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(users, "update_current_user_email", fake_update)
    try:
        response = client.patch(
            "/api/v1/users/me",
            json={"email": " NEW@EXAMPLE.COM "},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "created_at", "updated_at"}
    assert response.json()["email"] == "new@example.com"
    update = observed["update"]
    assert isinstance(update, CurrentUserEmailUpdate)
    assert update.email == "new@example.com"
    assert observed["user"] is user
    assert observed["session"] is session
    assert "password_hash" not in response.text
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    ("headers", "invalid_token"),
    [
        ({}, False),
        ({"Authorization": "Bearer controlled.invalid.token"}, True),
    ],
    ids=("missing", "invalid"),
)
def test_current_user_email_patch_requires_valid_authentication(
    headers: dict[str, str],
    invalid_token: bool,
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unauthenticated writes through the existing uniform boundary."""

    _session, lifecycle = current_user_session
    if invalid_token:
        monkeypatch.setattr(
            dependencies,
            "validate_access_token",
            MagicMock(side_effect=AccessTokenError(ACCESS_TOKEN_ERROR_MESSAGE)),
        )

    response = client.patch(
        "/api/v1/users/me",
        json={"email": "new@example.com"},
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
    assert response.headers["www-authenticate"] == "Bearer"
    assert lifecycle == ["opened", "closed"]


def test_current_user_email_patch_maps_duplicate_to_safe_409(
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose no canonical input or database detail from either conflict path."""

    _session, lifecycle = current_user_session
    user = make_user()

    def reject_duplicate(
        _update: CurrentUserEmailUpdate,
        _user: User,
        _session: Session,
    ) -> PublicUser:
        raise DuplicateEmailError(DUPLICATE_EMAIL_MESSAGE)

    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(users, "update_current_user_email", reject_duplicate)
    try:
        response = client.patch(
            "/api/v1/users/me",
            json={"email": " Taken@EXAMPLE.COM "},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 409
    assert response.json() == {"detail": DUPLICATE_EMAIL_MESSAGE}
    assert "Taken@EXAMPLE.COM" not in response.text
    assert "uq_users_email" not in response.text
    assert "sql" not in response.text.casefold()
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "invalid-email"},
        {"email": "user@example.com", "user_id": str(uuid4())},
    ],
    ids=("missing", "invalid", "extra"),
)
def test_current_user_email_patch_returns_422_before_service(
    payload: dict[str, object],
    client: TestClient,
    current_user_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject invalid write shapes without entering the update use case."""

    _session, lifecycle = current_user_session
    service = MagicMock()
    app.dependency_overrides[get_current_user] = make_user
    monkeypatch.setattr(users, "update_current_user_email", service)
    try:
        response = client.patch("/api/v1/users/me", json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


def test_current_user_route_is_versioned_with_only_planned_methods(
    client: TestClient,
) -> None:
    """Expose only the planned versioned GET and PATCH operations."""

    assert client.get("/users/me").status_code == 404
    assert client.get("/api/users/me").status_code == 404
    assert client.post("/api/v1/users/me").status_code == 405


def test_current_user_openapi_declares_bearer_and_public_allowlist() -> None:
    """Publish one Bearer-protected GET without internal identity material."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/users/me"]["get"]
    public_user = schema["components"]["schemas"]["PublicUser"]
    security_scheme = schema["components"]["securitySchemes"]["BearerAuth"]

    assert set(schema["paths"]["/api/v1/users/me"]) == {"get", "patch"}
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


def test_current_user_email_patch_openapi_is_strict_and_bearer_protected() -> None:
    """Document the one-field write and its complete public response surface."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/users/me"]["patch"]
    update_schema = schema["components"]["schemas"]["CurrentUserEmailUpdate"]

    assert operation["requestBody"]["required"] is True
    assert set(operation["responses"]) == {"200", "401", "409", "422"}
    assert operation["security"] == [{"BearerAuth": []}]
    assert set(update_schema["properties"]) == {"email"}
    assert update_schema["required"] == ["email"]
    assert update_schema["additionalProperties"] is False
    assert "password" not in str(update_schema)
    assert "user_id" not in str(update_schema)
    assert "password_hash" not in str(operation)
