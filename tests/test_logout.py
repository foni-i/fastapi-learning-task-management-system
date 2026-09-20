"""Presented-credential revocation, idempotency and safe HTTP boundaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth
from app.core.config import Settings
from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    AuthenticationUnavailableError,
)
from app.core.refresh_tokens import hash_refresh_token
from app.db.session import get_session
from app.main import create_app
from app.models import RefreshToken
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.services.refresh_tokens import logout_refresh_token

TOKEN = "l" * 43
PATH = "/api/v1/auth/logout"
NOW = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.mark.parametrize("state", ["active", "expired", "future", "missing", "revoked"])
def test_logout_owns_one_transaction_and_only_presented_row(state: str) -> None:
    session = MagicMock(spec=Session)
    repository = Mock(spec=RefreshTokenRepository)
    record = RefreshToken(
        id=uuid4(),
        user_id=uuid4(),
        token_hash=hash_refresh_token(SecretStr(TOKEN)),
        created_at=NOW + timedelta(days=1)
        if state == "future"
        else NOW - timedelta(days=8),
        expires_at=NOW - timedelta(days=1)
        if state == "expired"
        else NOW + timedelta(days=7),
        revoked_at=NOW if state == "revoked" else None,
    )
    repository.get_by_hash_for_update.return_value = (
        None if state == "missing" else record
    )
    repository.revoke_if_unrevoked.return_value = True
    events: list[str] = []

    def lookup(_: str) -> RefreshToken | None:
        events.append("lock")
        return None if state == "missing" else record

    repository.get_by_hash_for_update.side_effect = lookup

    def clock() -> datetime:
        events.append("clock")
        return NOW

    logout_refresh_token(
        SecretStr(TOKEN), session, clock=clock, repository_factory=lambda _: repository
    )
    repository.get_by_hash_for_update.assert_called_once_with(
        hash_refresh_token(SecretStr(TOKEN))
    )
    if state in {"missing", "revoked"}:
        repository.revoke_if_unrevoked.assert_not_called()
        assert events == ["lock"]
    else:
        repository.revoke_if_unrevoked.assert_called_once_with(
            token_id=record.id,
            user_id=record.user_id,
            revoked_at=max(NOW, record.created_at),
        )
        assert events == ["lock", "clock"]
    repository.create.assert_not_called()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["lookup", "update", "not_updated", "commit", "rollback", "clock"]
)
def test_logout_failures_are_rolled_back_and_sanitized(failure: str) -> None:
    session = MagicMock(spec=Session)
    repository = Mock(spec=RefreshTokenRepository)
    repository.get_by_hash_for_update.return_value = RefreshToken(
        id=uuid4(),
        user_id=uuid4(),
        created_at=NOW,
        revoked_at=None,
    )
    repository.revoke_if_unrevoked.return_value = failure != "not_updated"
    if failure in {"lookup", "rollback"}:
        repository.get_by_hash_for_update.side_effect = RuntimeError(TOKEN)
    if failure == "rollback":
        session.rollback.side_effect = RuntimeError(TOKEN)
    if failure == "update":
        repository.revoke_if_unrevoked.side_effect = RuntimeError(TOKEN)
    if failure == "commit":
        session.commit.side_effect = RuntimeError(TOKEN)
    with pytest.raises(AuthenticationUnavailableError) as error:
        logout_refresh_token(
            SecretStr(TOKEN),
            session,
            clock=lambda: NOW.replace(tzinfo=None) if failure == "clock" else NOW,
            repository_factory=lambda _: repository,
        )
    assert str(error.value) == AUTHENTICATION_UNAVAILABLE_MESSAGE
    session.rollback.assert_called_once_with()
    if failure != "commit":
        session.commit.assert_not_called()


def test_logout_repository_scopes_update_without_owning_transaction() -> None:
    session = MagicMock(spec=Session)
    owner, token_id = uuid4(), uuid4()
    assert RefreshTokenRepository(session).revoke_if_unrevoked(
        token_id=token_id, user_id=owner, revoked_at=NOW
    )
    statement = session.scalar.call_args.args[0]
    sql = str(statement)
    assert "refresh_tokens.id =" in sql and "refresh_tokens.user_id =" in sql
    assert "revoked_at IS NULL" in sql and "expires_at" not in sql
    assert owner in statement.compile().params.values()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"refresh_token": None},
        {"refresh_token": 1},
        {"refresh_token": ""},
        {"refresh_token": "a" * 42},
        {"refresh_token": "a" * 44},
        {"refresh_token": "!" * 43},
        {"refresh_token": [TOKEN]},
        {"refresh_token": TOKEN, "user_id": "chosen"},
        {TOKEN: TOKEN},
        [TOKEN],
        TOKEN,
    ],
)
def test_logout_invalid_body_is_bounded_and_never_calls_service(
    body: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    service = Mock()
    monkeypatch.setattr(auth, "logout_refresh_token", service)
    with TestClient(application) as client:
        response = client.post(PATH, json=body)
    assert response.status_code == 422
    assert (
        TOKEN not in response.text
        and "input" not in response.text
        and "ctx" not in response.text
    )
    assert len(response.json()["detail"]) <= 3
    assert response.headers["cache-control"] == "no-store"
    service.assert_not_called()


@pytest.mark.parametrize("failure", [None, "service", "dependency"])
def test_logout_empty_success_and_safe_failure_even_in_debug(
    failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app(Settings(debug=True))
    dependency = Mock(return_value=MagicMock(spec=Session))

    def session_dependency() -> Session:
        result = dependency()
        assert isinstance(result, Session)
        return result

    application.dependency_overrides[get_session] = session_dependency
    service = Mock(side_effect=RuntimeError(TOKEN) if failure == "service" else None)
    if failure == "dependency":
        dependency.side_effect = RuntimeError(TOKEN)
    monkeypatch.setattr(auth, "logout_refresh_token", service)
    with TestClient(application) as client:
        response = client.post(
            PATH,
            json={"refresh_token": TOKEN},
            headers={"Authorization": "Bearer expired-test-only"},
        )
    assert response.status_code == (503 if failure else 204)
    if failure:
        assert response.json() == {"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE}
    else:
        assert response.content == b""
        assert service.call_args.args[0].get_secret_value() == TOKEN
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert "set-cookie" not in response.headers
    assert TOKEN not in caplog.text


def test_logout_input_sources_and_openapi() -> None:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    with TestClient(application) as client:
        for content in (b'{"secret":', b"\xff\xfe\xff"):
            response = client.post(
                PATH, content=content, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 422 and "secret" not in response.text
            assert response.headers["pragma"] == "no-cache"
        assert (
            client.post(
                PATH,
                params={"refresh_token": "placeholder"},
                headers={
                    "Cookie": "refresh_token=placeholder",
                    "Authorization": "Bearer placeholder",
                },
            ).status_code
            == 422
        )
        assert client.get(PATH).status_code == 405
    operation = application.openapi()["paths"][PATH]["post"]
    assert not operation.get("security")
    assert set(operation["responses"]) == {"204", "422", "503"}
    assert "content" not in operation["responses"]["204"]
