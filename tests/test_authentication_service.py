"""Unit tests for authentication orchestration and single-transaction token-pair delivery."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    AuthenticationUnavailableError,
    InvalidCredentialsError,
)
from app.repositories.users import UserRepository
from app.schemas.auth import TokenPairResponse, UserLoginRequest
from app.services.authentication import authenticate_user

CONTROLLED_PASSWORD = "controlled login password"


class ControlledRepository:
    """Record the canonical lookup while returning a controlled user."""

    def __init__(self, user: SimpleNamespace | None) -> None:
        self.user = user
        self.received_email: str | None = None

    def get_by_email_for_update(self, email: str) -> SimpleNamespace | None:
        self.received_email = email
        return self.user


def make_credentials() -> UserLoginRequest:
    return UserLoginRequest.model_validate(
        {
            "email": " User@EXAMPLE.COM ",
            "password": CONTROLLED_PASSWORD,
        }
    )


def test_authentication_verifies_hash_and_commits_token_pair() -> None:
    """Coordinate lookup, verification and issuance within one transaction."""

    session = MagicMock(spec=Session)
    user_id = uuid4()
    stored_hash = "$argon2id$controlled-stored-hash"
    user = SimpleNamespace(id=user_id, password_hash=stored_hash)
    repository = ControlledRepository(user)
    events: list[str] = []

    def verify(password: str, password_hash: str) -> bool:
        events.append("verify")
        assert password == CONTROLLED_PASSWORD
        assert password_hash == stored_hash
        return True

    def issue(received_user_id: UUID) -> str:
        events.append("issue")
        assert received_user_id == user_id
        return "controlled.compact.token"

    result = authenticate_user(
        make_credentials(),
        session,
        password_verifier=verify,
        token_issuer=issue,
        repository_factory=lambda received_session: (
            cast(UserRepository, repository)
            if received_session is session
            else pytest.fail("unexpected session")
        ),
    )

    assert isinstance(result, TokenPairResponse)
    assert result.access_token == "controlled.compact.token"
    assert result.token_type == "bearer"
    assert len(result.refresh_token) == 43
    session.add.assert_called_once()
    session.flush.assert_called_once()
    assert repository.received_email == "user@example.com"
    assert events == ["verify", "issue"]
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("failure", ["missing-account", "wrong-password"])
def test_authentication_uses_one_failure_for_both_invalid_credentials(
    failure: str,
) -> None:
    """Do not expose account existence and never issue a token on failure."""

    session = MagicMock(spec=Session)
    user = None
    if failure == "wrong-password":
        user = SimpleNamespace(id=uuid4(), password_hash="$argon2id$stored")
    repository = ControlledRepository(user)
    verifier = Mock(return_value=False)
    issuer = Mock()

    with pytest.raises(InvalidCredentialsError) as exc_info:
        authenticate_user(
            make_credentials(),
            session,
            password_verifier=verifier,
            token_issuer=issuer,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == INVALID_CREDENTIALS_MESSAGE
    assert CONTROLLED_PASSWORD not in str(exc_info.value)
    if failure == "missing-account":
        verifier.assert_not_called()
    else:
        verifier.assert_called_once_with(CONTROLLED_PASSWORD, "$argon2id$stored")
    issuer.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


def test_authentication_bounds_unexpected_verifier_failure() -> None:
    """Translate operational failures safely, distinct from invalid credentials."""

    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled verifier failure")
    repository = ControlledRepository(
        SimpleNamespace(id=uuid4(), password_hash="$argon2id$stored")
    )

    with pytest.raises(AuthenticationUnavailableError) as exc_info:
        authenticate_user(
            make_credentials(),
            session,
            password_verifier=Mock(side_effect=failure),
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == AUTHENTICATION_UNAVAILABLE_MESSAGE
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
