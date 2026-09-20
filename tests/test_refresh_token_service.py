"""Service transaction ownership and safe internal credential delivery."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.exceptions import (
    REFRESH_TOKEN_ISSUANCE_MESSAGE,
    RefreshTokenIssuanceError,
)
from app.core.refresh_tokens import hash_refresh_token
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.services.refresh_tokens import issue_refresh_token


def test_issuance_commits_once_before_delivering_redacted_value() -> None:
    events: list[str] = []
    session = MagicMock(spec=Session)
    repository = Mock(spec=RefreshTokenRepository)
    owner = uuid4()
    token = SecretStr("a" * 43)
    now = datetime(2026, 9, 17, 8, tzinfo=timezone(timedelta(hours=8)))
    repository.create.side_effect = lambda **kwargs: events.append("insert")
    session.commit.side_effect = lambda: events.append("commit")
    result = issue_refresh_token(
        owner,
        session,
        issued_at=now,
        token_generator=lambda: token,
        repository_factory=lambda _: repository,
    )
    events.append("return")
    assert events == ["insert", "commit", "return"]
    repository.create.assert_called_once_with(
        user_id=owner,
        token_hash=hash_refresh_token(token),
        created_at=now.astimezone(UTC),
        expires_at=now.astimezone(UTC) + timedelta(days=7),
    )
    assert result.token is token
    assert result.expires_at == now.astimezone(UTC) + timedelta(days=7)
    assert result.expires_at.tzinfo is UTC
    assert token.get_secret_value() not in repr(result)
    assert hash_refresh_token(token) not in repr(result)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.refresh.assert_not_called()


@pytest.mark.parametrize(
    "stage",
    ["entropy", "format", "factory", "flush", "commit", "rollback", "naive-time"],
)
def test_any_issuance_failure_rolls_back_without_returning_or_logging_secret(
    stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    session = MagicMock(spec=Session)
    repository = Mock(spec=RefreshTokenRepository)
    token = SecretStr("b" * 43)
    secret_failure = RuntimeError(token.get_secret_value())
    generator = Mock(return_value=token)
    factory = Mock(return_value=repository)
    now = datetime(2026, 9, 17, tzinfo=UTC)
    if stage == "entropy":
        generator.side_effect = secret_failure
    elif stage == "format":
        generator.return_value = SecretStr("invalid")
    elif stage == "factory":
        factory.side_effect = secret_failure
    elif stage == "flush":
        repository.create.side_effect = secret_failure
    elif stage in ("commit", "rollback"):
        session.commit.side_effect = secret_failure
        if stage == "rollback":
            session.rollback.side_effect = secret_failure
    elif stage == "naive-time":
        now = now.replace(tzinfo=None)

    with pytest.raises(RefreshTokenIssuanceError) as error:
        issue_refresh_token(
            uuid4(),
            session,
            issued_at=now,
            token_generator=generator,
            repository_factory=factory,
        )
    assert str(error.value) == REFRESH_TOKEN_ISSUANCE_MESSAGE
    assert error.value.__suppress_context__ is True
    assert token.get_secret_value() not in caplog.text
    session.rollback.assert_called_once_with()
    if stage not in ("commit", "rollback"):
        session.commit.assert_not_called()
    if stage in ("entropy", "format", "naive-time"):
        factory.assert_not_called()
