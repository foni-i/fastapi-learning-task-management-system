"""Unit tests for Bearer authentication and current-user resolution."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api import dependencies
from app.core.exceptions import AUTHENTICATION_REQUIRED_MESSAGE
from app.core.tokens import (
    ACCESS_TOKEN_ERROR_MESSAGE,
    AccessTokenClaims,
    AccessTokenError,
)
from app.models.user import User


class ControlledRepository:
    """Capture a UUID lookup without adding persistence or HTTP behavior."""

    result: User | None = None
    received_session: Session | None = None
    received_user_id: object = None

    def __init__(self, session: Session) -> None:
        type(self).received_session = session

    def get_by_id(self, user_id: object) -> User | None:
        type(self).received_user_id = user_id
        return type(self).result


def make_claims() -> AccessTokenClaims:
    now = datetime.now(UTC)
    return AccessTokenClaims(
        subject=uuid4(),
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def make_credentials(
    *,
    scheme: str = "Bearer",
    token: str = "controlled.compact.token",
) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


def assert_safe_401(error: HTTPException, secret_value: str = "") -> None:
    assert error.status_code == 401
    assert error.detail == AUTHENTICATION_REQUIRED_MESSAGE
    assert error.headers == {"WWW-Authenticate": "Bearer"}
    if secret_value:
        assert secret_value not in str(error)


def test_validated_claims_resolve_the_exact_current_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use only a fully validated subject for one read-only repository lookup."""

    session = MagicMock(spec=Session)
    claims = make_claims()
    expected_user = cast(User, SimpleNamespace(id=claims.subject))
    ControlledRepository.result = expected_user
    monkeypatch.setattr(dependencies, "UserRepository", ControlledRepository)
    monkeypatch.setattr(dependencies, "validate_access_token", lambda _token: claims)

    result = dependencies.get_current_user(make_credentials(), session)

    assert result is expected_user
    assert ControlledRepository.received_session is session
    assert ControlledRepository.received_user_id == claims.subject
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "credentials",
    [
        None,
        make_credentials(scheme="Basic"),
        make_credentials(token=""),
    ],
    ids=("missing", "wrong-scheme", "empty"),
)
def test_missing_or_malformed_bearer_credentials_share_one_401(
    credentials: HTTPAuthorizationCredentials | None,
) -> None:
    """Reject header-level failures before token validation or lookup."""

    session = MagicMock(spec=Session)

    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_current_user(credentials, session)

    assert_safe_401(exc_info.value)
    session.scalar.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "invalid_path",
    [
        "expired",
        "tampered",
        "wrong-type",
        "wrong-issuer",
        "wrong-audience",
        "wrong-algorithm",
        "invalid-subject",
    ],
)
def test_invalid_token_paths_share_one_non_disclosing_401(
    invalid_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate every primitive validation failure before database lookup."""

    session = MagicMock(spec=Session)
    token = f"controlled.{invalid_path}.token"

    def reject(_token: str) -> AccessTokenClaims:
        raise AccessTokenError(ACCESS_TOKEN_ERROR_MESSAGE)

    monkeypatch.setattr(dependencies, "validate_access_token", reject)

    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_current_user(make_credentials(token=token), session)

    assert_safe_401(exc_info.value, token)
    session.scalar.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_missing_persisted_user_uses_the_same_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not reveal whether a valid token subject still has an account."""

    session = MagicMock(spec=Session)
    claims = make_claims()
    ControlledRepository.result = None
    monkeypatch.setattr(dependencies, "UserRepository", ControlledRepository)
    monkeypatch.setattr(dependencies, "validate_access_token", lambda _token: claims)

    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_current_user(make_credentials(), session)

    assert_safe_401(exc_info.value)
    assert ControlledRepository.received_user_id == claims.subject
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
