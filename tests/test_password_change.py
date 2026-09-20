"""Password change HTTP redaction, transaction boundaries and serialization SQL."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import users
from app.core.config import Settings
from app.core.exceptions import AuthenticationUnavailableError, InvalidCredentialsError
from app.db.session import get_session
from app.main import create_app
from app.models import User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.user import PasswordChangeRequest
from app.services import password_change

PATH = "/api/v1/users/me/change-password"
OLD = "test-only old password"
NEW = "test-only new password"


@pytest.mark.parametrize(
    "value", ["", "a" * 11, "a" * 129, " " * 12, None, 12, [NEW], OLD]
)
def test_new_password_policy_and_distinctness(value: object) -> None:
    with pytest.raises(ValidationError):
        PasswordChangeRequest.model_validate(
            {"current_password": OLD, "new_password": value}
        )


@pytest.mark.parametrize("length", [12, 128])
def test_password_boundaries_preserve_exact_characters(length: int) -> None:
    value = " " + "密" * (length - 2) + " "
    request = PasswordChangeRequest.model_validate(
        {"current_password": "x", "new_password": value}
    )
    assert request.new_password.get_secret_value() == value
    assert value not in repr(request)


@pytest.mark.parametrize(
    "failure",
    [None, "missing", "wrong", "lock", "hash", "write", "revoke", "commit", "rollback"],
)
def test_password_and_revocation_share_one_transaction(
    failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    owner = uuid4()
    user = User(id=owner, password_hash="test-only stored hash")
    repo = Mock(spec=UserRepository)
    refresh = Mock(spec=RefreshTokenRepository)
    events: list[str] = []

    def locked(_: object) -> User | None:
        events.append("lock")
        if failure in {"lock", "rollback"}:
            raise RuntimeError(OLD)
        return None if failure == "missing" else user

    def verify(plaintext: str, digest: str) -> bool:
        events.append("verify")
        assert plaintext == OLD and digest == user.password_hash
        return failure != "wrong"

    def hashed(plaintext: str) -> str:
        events.append("hash")
        assert plaintext == NEW
        if failure == "hash":
            raise RuntimeError(NEW)
        return "test-only replacement hash"

    def step(name: str) -> None:
        events.append(name)
        if failure == name:
            raise RuntimeError(NEW)

    repo.get_by_id_for_update.side_effect = locked
    repo.update_password.side_effect = lambda *a, **kw: step("write")
    refresh.revoke_all_for_owner.side_effect = lambda *a, **kw: step("revoke")
    session.commit.side_effect = lambda: step("commit")
    if failure == "rollback":
        session.rollback.side_effect = RuntimeError(OLD)
    monkeypatch.setattr(password_change, "UserRepository", Mock(return_value=repo))
    monkeypatch.setattr(
        password_change, "RefreshTokenRepository", Mock(return_value=refresh)
    )
    credentials = PasswordChangeRequest.model_validate(
        {"current_password": OLD, "new_password": NEW}
    )

    def run() -> None:
        password_change.change_password(
            credentials,
            owner,
            session,
            password_verifier=verify,
            password_hasher=hashed,
        )

    if failure is None:
        run()
        assert events == ["lock", "verify", "hash", "write", "revoke", "commit"]
        assert refresh.revoke_all_for_owner.call_args.args == (owner,)
        assert repo.update_password.call_args.kwargs["updated_at"].tzinfo is UTC
        session.rollback.assert_not_called()
    else:
        expected = (
            InvalidCredentialsError
            if failure in {"missing", "wrong"}
            else AuthenticationUnavailableError
        )
        with pytest.raises(expected) as error:
            run()
        assert OLD not in str(error.value) and NEW not in str(error.value)
        session.rollback.assert_called_once_with()
        if failure != "commit":
            session.commit.assert_not_called()


def test_sql_locks_refreshes_and_bulk_revokes_only_owner() -> None:
    session = MagicMock(spec=Session)
    owner = uuid4()
    users_repo = UserRepository(session)
    users_repo.get_by_id_for_update(owner)
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement)
    assert statement.compile().params == {"id_1": owner}
    assert statement.get_execution_options()["populate_existing"] is True
    users_repo.get_by_email_for_update("a@example.com")
    assert "FOR UPDATE" in str(session.scalar.call_args.args[0])
    RefreshTokenRepository(session).revoke_all_for_owner(
        owner, revoked_at=datetime.now(UTC)
    )
    statement = session.execute.call_args.args[0]
    assert "refresh_tokens.user_id =" in str(statement)
    assert "revoked_at IS NULL" in str(statement)
    assert "greatest(" in str(statement)
    assert owner in statement.compile().params.values()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"current_password": OLD},
        {"current_password": None, "new_password": NEW},
        {"current_password": "a" * 129, "new_password": NEW},
        {"current_password": [OLD], "new_password": NEW},
        {"current_password": OLD, "new_password": NEW, "user_id": "chosen"},
        {"current_password": OLD, "new_password": NEW, OLD: NEW},
        [OLD, NEW],
    ],
)
def test_http_invalid_password_inputs_never_echo(
    body: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    application.dependency_overrides[get_current_user] = lambda: User(id=uuid4())
    service = Mock()
    monkeypatch.setattr(users, "change_password", service)
    with TestClient(application) as client:
        response = client.post(PATH, json=body)
    assert response.status_code == 422
    assert OLD not in response.text and NEW not in response.text
    assert "input" not in response.text and "ctx" not in response.text
    assert len(response.json()["detail"]) <= 3
    assert response.headers["cache-control"] == "no-store"
    service.assert_not_called()


@pytest.mark.parametrize(
    "failure", [None, "password", "service", "dependency", "unauthenticated"]
)
def test_http_identity_status_and_error_safety(
    failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = create_app(Settings(debug=True))
    owner = User(id=uuid4())
    application.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)

    def identity() -> User:
        if failure == "dependency":
            raise RuntimeError(OLD)
        return owner

    if failure != "unauthenticated":
        application.dependency_overrides[get_current_user] = identity
    service = Mock()
    if failure == "password":
        service.side_effect = InvalidCredentialsError(OLD)
    elif failure == "service":
        service.side_effect = RuntimeError(NEW)
    monkeypatch.setattr(users, "change_password", service)
    with TestClient(application) as client:
        response = client.post(
            PATH, json={"current_password": OLD, "new_password": NEW}
        )
    expected = (
        204
        if failure is None
        else 401
        if failure in {"password", "unauthenticated"}
        else 503
    )
    assert response.status_code == expected
    assert OLD not in response.text and NEW not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    if failure is None:
        assert response.content == b""
        assert service.call_args.args[1] == owner.id
    elif expected == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    operation = application.openapi()["paths"][PATH]["post"]
    assert operation["security"] == [{"BearerAuth": []}]
    assert set(operation["responses"]) == {"204", "401", "422", "503"}
    assert "content" not in operation["responses"]["204"]
