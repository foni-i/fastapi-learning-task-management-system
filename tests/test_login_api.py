"""Connection-free API tests for the versioned login endpoint."""

from collections.abc import Iterator
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth
from app.core.exceptions import INVALID_CREDENTIALS_MESSAGE, InvalidCredentialsError
from app.db.session import get_session
from app.main import app
from app.schemas.auth import AccessTokenResponse, UserLoginRequest

CONTROLLED_PASSWORD = "controlled login password"


@pytest.fixture
def login_session() -> Iterator[tuple[MagicMock, list[str]]]:
    """Override the dependency with one tracked synchronous Session."""

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


def test_login_returns_200_and_exact_token_fields(
    client: TestClient,
    login_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass normalized credentials and the request Session to the service."""

    session, lifecycle = login_session
    observed: dict[str, object] = {}

    def fake_authenticate(
        credentials: UserLoginRequest,
        received_session: Session,
    ) -> AccessTokenResponse:
        observed["credentials"] = credentials
        observed["session"] = received_session
        return AccessTokenResponse(access_token="controlled.compact.token")

    monkeypatch.setattr(auth, "authenticate_user", fake_authenticate)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": " User@EXAMPLE.COM ", "password": CONTROLLED_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "controlled.compact.token",
        "token_type": "bearer",
    }
    credentials = observed["credentials"]
    assert isinstance(credentials, UserLoginRequest)
    assert credentials.email == "user@example.com"
    assert credentials.password.get_secret_value() == CONTROLLED_PASSWORD
    assert observed["session"] is session
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("failure_path", ["missing-account", "wrong-password"])
def test_invalid_credentials_return_the_same_safe_401(
    failure_path: str,
    client: TestClient,
    login_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map every credential failure to one body and bearer challenge."""

    _session, lifecycle = login_session

    def reject_credentials(
        _credentials: UserLoginRequest,
        _session: Session,
    ) -> AccessTokenResponse:
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

    monkeypatch.setattr(auth, "authenticate_user", reject_credentials)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": CONTROLLED_PASSWORD},
    )

    assert failure_path in {"missing-account", "wrong-password"}
    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS_MESSAGE}
    assert response.headers["www-authenticate"] == "Bearer"
    assert CONTROLLED_PASSWORD not in response.text
    assert lifecycle == ["opened", "closed"]


def test_login_validation_masks_password_and_skips_service(
    client: TestClient,
    login_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject invalid input at the schema boundary without reflecting its secret."""

    _session, lifecycle = login_session
    service = Mock()
    rejected_password = "x" * 129
    monkeypatch.setattr(auth, "authenticate_user", service)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": rejected_password},
    )

    assert response.status_code == 422
    assert rejected_password not in response.text
    assert response.json()["detail"][0]["input"] == "**********"
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


def test_login_service_error_still_closes_session(
    client: TestClient,
    login_session: tuple[MagicMock, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the dependency Session when an unexpected failure propagates."""

    _session, lifecycle = login_session

    def fail_authentication(
        _credentials: UserLoginRequest,
        _session: Session,
    ) -> AccessTokenResponse:
        raise RuntimeError("safe controlled failure")

    monkeypatch.setattr(auth, "authenticate_user", fail_authentication)
    with pytest.raises(RuntimeError, match="safe controlled failure"):
        client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": CONTROLLED_PASSWORD},
        )

    assert lifecycle == ["opened", "closed"]


def test_login_route_is_versioned_and_post_only(
    client: TestClient,
    login_session: tuple[MagicMock, list[str]],
) -> None:
    """Expose no unversioned route or additional HTTP method."""

    assert client.post("/auth/login", json={}).status_code == 404
    assert client.post("/login", json={}).status_code == 404
    assert client.get("/api/v1/auth/login").status_code == 405


def test_login_openapi_has_bounded_request_response_and_error_contracts() -> None:
    """Document only login input, bearer output and planned response statuses."""

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/auth/login"]["post"]
    schemas = schema["components"]["schemas"]

    assert set(schema["paths"]["/api/v1/auth/login"]) == {"post"}
    assert operation["requestBody"]["required"] is True
    assert set(operation["responses"]) == {"200", "401", "422"}
    assert set(schemas["UserLoginRequest"]["properties"]) == {"email", "password"}
    assert set(schemas["AccessTokenResponse"]["properties"]) == {
        "access_token",
        "token_type",
    }
    assert "password_hash" not in str(operation)
    assert "/api/v1/auth/refresh" not in schema["paths"]
    assert "/api/v1/users/me" not in schema["paths"]
