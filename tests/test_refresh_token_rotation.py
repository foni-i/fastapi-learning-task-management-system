"""Rotation ordering, credential rejection and safe failure contracts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import Select, Update
from sqlalchemy.orm import Session

from app.core.exceptions import (
    REFRESH_TOKEN_ROTATION_MESSAGE,
    InvalidRefreshTokenError,
    RefreshTokenRotationError,
)
from app.core.refresh_tokens import INVALID_REFRESH_TOKEN_MESSAGE, hash_refresh_token
from app.models import RefreshToken
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.services.refresh_tokens import rotate_refresh_token

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def active_record() -> RefreshToken:
    return RefreshToken(
        id=uuid4(),
        user_id=uuid4(),
        token_hash="a" * 64,
        created_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
    )


def test_repository_locks_exact_digest_and_refreshes_identity_map() -> None:
    session = MagicMock(spec=Session)
    expected = active_record()
    session.scalar.side_effect = [expected.user_id, expected]
    assert RefreshTokenRepository(session).get_by_hash_for_update("a" * 64) is expected
    assert str(session.execute.call_args.args[0]) == "SET LOCAL lock_timeout = '5s'"
    statement = session.scalar.call_args.args[0]
    assert isinstance(statement, Select)
    compiled = statement.compile()
    assert "FOR UPDATE" in str(compiled)
    assert "refresh_tokens.token_hash = :token_hash_1" in str(statement.whereclause)
    assert compiled.params == {"token_hash_1": "a" * 64, "user_id_1": expected.user_id}
    assert statement.get_execution_options()["populate_existing"] is True
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("updated", [True, False])
def test_repository_revocation_is_owner_scoped_and_conditional(updated: bool) -> None:
    session = MagicMock(spec=Session)
    identifier, owner = uuid4(), uuid4()
    session.scalar.return_value = identifier if updated else None
    assert (
        RefreshTokenRepository(session).revoke_if_active(
            token_id=identifier, user_id=owner, revoked_at=NOW
        )
        is updated
    )
    statement = session.scalar.call_args.args[0]
    assert isinstance(statement, Update)
    where = str(statement.whereclause)
    for predicate in (
        "refresh_tokens.id =",
        "refresh_tokens.user_id =",
        "refresh_tokens.revoked_at IS NULL",
        "refresh_tokens.expires_at >",
        "refresh_tokens.created_at <=",
    ):
        assert predicate in where
    assert statement.compile().params == {
        "id_1": identifier,
        "user_id_1": owner,
        "revoked_at": NOW,
        "expires_at_1": NOW,
        "created_at_1": NOW,
    }
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_rotation_locks_before_time_check_and_commits_before_return() -> None:
    events: list[str] = []
    session = MagicMock(spec=Session)
    repo = Mock(spec=RefreshTokenRepository)
    record = active_record()
    old, new = SecretStr("a" * 43), SecretStr("b" * 43)

    def lock(digest: str) -> RefreshToken:
        assert digest == hash_refresh_token(old)
        events.append("lock")
        return record

    def clock() -> datetime:
        events.append("clock")
        return NOW

    def revoke(**kwargs: object) -> bool:
        events.append("revoke")
        return True

    def generate() -> SecretStr:
        events.append("generate")
        return new

    repo.get_by_hash_for_update.side_effect = lock
    repo.revoke_if_active.side_effect = revoke
    repo.create.side_effect = lambda **kwargs: events.append("insert")
    session.commit.side_effect = lambda: events.append("commit")
    result = rotate_refresh_token(
        old,
        session,
        clock=clock,
        token_generator=generate,
        repository_factory=lambda _: repo,
    )
    events.append("return")
    assert events == [
        "lock",
        "clock",
        "revoke",
        "generate",
        "insert",
        "commit",
        "return",
    ]
    assert result.user_id == record.user_id
    assert result.token is new
    assert result.expires_at == NOW + timedelta(days=7)
    repo.create.assert_called_once_with(
        user_id=record.user_id,
        token_hash=hash_refresh_token(new),
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    assert old.get_secret_value() not in repr(result)
    assert new.get_secret_value() not in repr(result)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "missing",
        "expired",
        "at-expiry",
        "revoked",
        "future",
        "lost-conditional-update",
    ],
)
def test_invalid_credentials_share_one_safe_error(case: str) -> None:
    session = MagicMock(spec=Session)
    repo = Mock(spec=RefreshTokenRepository)
    record = active_record()
    repo.get_by_hash_for_update.return_value = record
    repo.revoke_if_active.return_value = case != "lost-conditional-update"
    token = SecretStr("a" * 43)
    if case == "malformed":
        token = SecretStr("invalid")
    elif case == "missing":
        repo.get_by_hash_for_update.return_value = None
    elif case in ("expired", "at-expiry"):
        record.expires_at = NOW - timedelta(seconds=1) if case == "expired" else NOW
    elif case == "revoked":
        record.revoked_at = NOW
    elif case == "future":
        record.created_at = NOW + timedelta(seconds=1)
    generator = Mock()
    with pytest.raises(InvalidRefreshTokenError) as error:
        rotate_refresh_token(
            token,
            session,
            clock=lambda: NOW,
            token_generator=generator,
            repository_factory=lambda _: repo,
        )
    assert str(error.value) == INVALID_REFRESH_TOKEN_MESSAGE
    generator.assert_not_called()
    repo.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize(
    "stage",
    ["lookup", "clock", "revoke", "entropy", "format", "insert", "commit", "rollback"],
)
def test_rotation_failures_are_safe_and_rolled_back(
    stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    session = MagicMock(spec=Session)
    repo = Mock(spec=RefreshTokenRepository)
    repo.get_by_hash_for_update.return_value = active_record()
    repo.revoke_if_active.return_value = True
    old = SecretStr("a" * 43)
    failure = RuntimeError(old.get_secret_value())
    generator = Mock(return_value=SecretStr("b" * 43))
    clock = Mock(return_value=NOW)
    if stage == "lookup":
        repo.get_by_hash_for_update.side_effect = failure
    elif stage == "clock":
        clock.return_value = NOW.replace(tzinfo=None)
    elif stage == "revoke":
        repo.revoke_if_active.side_effect = failure
    elif stage == "entropy":
        generator.side_effect = failure
    elif stage == "format":
        generator.return_value = SecretStr("invalid")
    elif stage == "insert":
        repo.create.side_effect = failure
    elif stage in ("commit", "rollback"):
        session.commit.side_effect = failure
        if stage == "rollback":
            session.rollback.side_effect = failure
    with pytest.raises(RefreshTokenRotationError) as error:
        rotate_refresh_token(
            old,
            session,
            clock=clock,
            token_generator=generator,
            repository_factory=lambda _: repo,
        )
    assert str(error.value) == REFRESH_TOKEN_ROTATION_MESSAGE
    assert error.value.__suppress_context__
    assert old.get_secret_value() not in caplog.text
    session.rollback.assert_called_once_with()
    if stage not in ("commit", "rollback"):
        session.commit.assert_not_called()
