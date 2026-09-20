"""Secret-safe JSON delivery, failure boundaries and token-pair transactions."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth
from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    AuthenticationUnavailableError,
    InvalidRefreshTokenError,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.auth import TokenPairResponse, UserLoginRequest
from app.services import authentication
from app.services.refresh_tokens import IssuedRefreshToken, RotatedRefreshToken

TOKEN = "r" * 43
NOW = datetime(2026, 9, 18, tzinfo=UTC)
PATH = "/api/v1/auth/refresh"


@pytest.fixture
def api() -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    with TestClient(application) as client:
        yield client


def test_body_only_refresh_delivers_exact_pair(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    result = TokenPairResponse(
        access_token="test-only-access",
        refresh_token="n" * 43,
        refresh_expires_at=NOW + timedelta(days=7),
    )
    service = Mock(return_value=result)
    monkeypatch.setattr(auth, "refresh_authentication", service)
    response = api.post(
        PATH,
        json={"refresh_token": TOKEN},
        headers={"Authorization": "Bearer expired-test-only"},
    )
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert service.call_args.args[0].get_secret_value() == TOKEN
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert "set-cookie" not in response.headers
    assert TOKEN not in caplog.text and result.refresh_token not in caplog.text
    assert result.access_token not in repr(result) and result.refresh_token not in repr(
        result
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"refresh_token": None},
        {"refresh_token": 42},
        {"refresh_token": "r" * 42},
        {"refresh_token": "r" * 44},
        {"refresh_token": " " + "r" * 42},
        {"refresh_token": "中" * 43},
        {"refresh_token": {TOKEN: TOKEN}},
        {"refresh_token": [TOKEN]},
        {"refresh_token": TOKEN, TOKEN: TOKEN},
        {"refresh_token": TOKEN, "user_id": "chosen"},
        [TOKEN],
        TOKEN,
    ],
)
def test_invalid_body_never_echoes_input_or_calls_service(
    body: object, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Mock()
    monkeypatch.setattr(auth, "refresh_authentication", service)
    response = api.post(PATH, json=body)
    assert response.status_code == 422
    assert (
        TOKEN not in response.text
        and "input" not in response.text
        and "ctx" not in response.text
    )
    assert len(response.json()["detail"]) <= 3
    assert response.headers["cache-control"] == "no-store"
    service.assert_not_called()


@pytest.mark.parametrize("path", [PATH, "/api/v1/auth/login"])
def test_malformed_json_and_field_names_are_safe(path: str, api: TestClient) -> None:
    for body in ('{"' + TOKEN + '":', '{"' + TOKEN + '": "' + TOKEN + '"}'):
        response = api.post(
            path, content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert TOKEN not in response.text
        assert response.headers["pragma"] == "no-cache"


def test_query_cookie_and_bearer_cannot_replace_body(api: TestClient) -> None:
    response = api.post(
        PATH,
        params={"refresh_token": "placeholder"},
        headers={
            "Cookie": "refresh_token=placeholder",
            "Authorization": "Bearer placeholder",
        },
    )
    assert response.status_code == 422
    assert api.get(PATH).status_code == 405
    assert api.post("/auth/refresh", json={}).status_code == 404


@pytest.mark.parametrize("path", [PATH, "/api/v1/auth/login"])
def test_invalid_json_encoding_is_safe_422(path: str, api: TestClient) -> None:
    response = api.post(
        path, content=b"\xff\xfe\xff", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "detail": [
            {
                "loc": ["body"],
                "type": "invalid_request",
                "msg": "Invalid authentication request",
            }
        ]
    }


@pytest.mark.parametrize(
    "failure,status",
    [(InvalidRefreshTokenError("private diagnostic"), 401), (RuntimeError(TOKEN), 503)],
)
def test_refresh_failures_are_fixed_and_uncached(
    failure: Exception, status: int, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "refresh_authentication", Mock(side_effect=failure))
    response = api.post(PATH, json={"refresh_token": TOKEN})
    assert response.status_code == status
    assert TOKEN not in response.text and "private diagnostic" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_dependency_setup_failure_is_safe_even_with_debug_enabled() -> None:
    from app.core.config import Settings

    application = create_app(Settings(debug=True))

    def unavailable() -> Session:
        raise RuntimeError(TOKEN)

    application.dependency_overrides[get_session] = unavailable
    with TestClient(application) as client:
        response = client.post(PATH, json={"refresh_token": TOKEN})
    assert response.status_code == 503
    assert response.json() == {"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind", ["login", "refresh"])
@pytest.mark.parametrize(
    "failure", [None, "prepare", "sign", "response", "commit", "rollback"]
)
def test_pair_preparation_signing_and_single_commit_order(
    kind: str, failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = MagicMock(spec=Session)
    owner = uuid4()
    events: list[str] = []

    def prepare(
        *args: object, **kwargs: object
    ) -> IssuedRefreshToken | RotatedRefreshToken:
        events.append("prepare")
        if failure == "prepare":
            raise RuntimeError(TOKEN)
        if kind == "login":
            return IssuedRefreshToken(SecretStr(TOKEN), NOW)
        return RotatedRefreshToken(owner, SecretStr(TOKEN), NOW)

    def sign(user_id: object) -> str:
        assert user_id == owner
        events.append("sign")
        if failure in {"sign", "rollback"}:
            raise RuntimeError(TOKEN)
        return "test-only-access"

    def commit() -> None:
        events.append("commit")
        if failure == "commit":
            raise RuntimeError(TOKEN)

    session.commit.side_effect = commit
    if failure == "rollback":
        session.rollback.side_effect = RuntimeError(TOKEN)
    monkeypatch.setattr(authentication, "_prepare_issuance", prepare)
    monkeypatch.setattr(authentication, "_prepare_rotation", prepare)
    if failure == "response":
        monkeypatch.setattr(
            authentication, "TokenPairResponse", Mock(side_effect=RuntimeError(TOKEN))
        )
    repo = Mock()
    repo.get_by_email_for_update.return_value = SimpleNamespace(
        id=owner, password_hash="test-only"
    )

    def run() -> TokenPairResponse:
        if kind == "login":
            return authentication.authenticate_user(
                UserLoginRequest(
                    email="a@example.com", password=SecretStr("test-only")
                ),
                session,
                password_verifier=lambda *_: True,
                token_issuer=sign,
                repository_factory=lambda _: repo,
            )
        return authentication.refresh_authentication(
            SecretStr(TOKEN), session, token_issuer=sign
        )

    if failure is None:
        result = run()
        assert result.refresh_token == TOKEN
        assert events == ["prepare", "sign", "commit"]
        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
    else:
        with pytest.raises(AuthenticationUnavailableError) as error:
            run()
        assert str(error.value) == AUTHENTICATION_UNAVAILABLE_MESSAGE
        session.rollback.assert_called_once_with()
        if failure != "commit":
            session.commit.assert_not_called()


def test_refresh_openapi_does_not_require_bearer_or_expose_storage() -> None:
    document = create_app().openapi()
    operation = document["paths"][PATH]["post"]
    assert not operation.get("security")
    assert set(operation["responses"]) == {"200", "401", "422", "503"}
    schemas = document["components"]["schemas"]
    assert set(schemas["RefreshTokenRequest"]["properties"]) == {"refresh_token"}
    field = schemas["RefreshTokenRequest"]["properties"]["refresh_token"]
    assert field["minLength"] == field["maxLength"] == 43
    assert field["writeOnly"] is True
    assert "token_hash" not in str(operation)
