"""Unit tests for the authenticated user's canonical email update."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.models.user import User
from app.repositories.users import UserRepository
from app.schemas.user import CurrentUserEmailUpdate, PublicUser
from app.services.current_user import update_current_user_email


class ControlledRepository:
    """Record email lookup/update behavior and optionally fail the write."""

    def __init__(
        self,
        user: User,
        *,
        existing_user: User | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.user = user
        self.existing_user = existing_user
        self.failure = failure
        self.lookup_email: str | None = None
        self.updated_email: str | None = None

    def get_by_email(self, email: str) -> User | None:
        self.lookup_email = email
        return self.existing_user

    def update_email(self, user: User, *, email: str) -> User:
        assert user is self.user
        self.updated_email = email
        if self.failure is not None:
            raise self.failure
        user.email = email
        return user


class ControlledDatabaseError(Exception):
    """Provide only the Psycopg diagnostic used by the service."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__("controlled database failure")
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def make_user(email: str = "old@example.com") -> User:
    timestamp = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)
    return cast(
        User,
        SimpleNamespace(
            id=uuid4(),
            email=email,
            password_hash="$argon2id$controlled-hash",
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def make_update(email: str = "new@example.com") -> CurrentUserEmailUpdate:
    return CurrentUserEmailUpdate.model_validate({"email": email})


def make_integrity_error(constraint_name: str) -> IntegrityError:
    return IntegrityError(
        "controlled statement",
        {},
        ControlledDatabaseError(constraint_name),
    )


def test_same_canonical_email_is_idempotent_without_transaction_work() -> None:
    """Return the allowlist without lookup, write, commit or rollback."""

    user = make_user()
    session = MagicMock(spec=Session)
    repository_factory = Mock()

    result = update_current_user_email(
        make_update(" OLD@EXAMPLE.COM "),
        user,
        session,
        repository_factory=repository_factory,
    )

    assert isinstance(result, PublicUser)
    assert result.email == "old@example.com"
    repository_factory.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_changed_email_updates_and_commits_without_touching_internal_fields() -> None:
    """Pass only canonical email to persistence and return the public allowlist."""

    user = make_user()
    original_id = user.id
    original_hash = user.password_hash
    original_created_at = user.created_at
    session = MagicMock(spec=Session)
    repository = ControlledRepository(user)

    result = update_current_user_email(
        make_update(" NEW@EXAMPLE.COM "),
        user,
        session,
        repository_factory=lambda received_session: (
            cast(UserRepository, repository)
            if received_session is session
            else pytest.fail("unexpected session")
        ),
    )

    assert repository.lookup_email == "new@example.com"
    assert repository.updated_email == "new@example.com"
    assert user.id == original_id
    assert user.password_hash == original_hash
    assert user.created_at == original_created_at
    assert result.model_dump().keys() == {"id", "email", "created_at", "updated_at"}
    assert "password_hash" not in result.model_dump_json()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_early_duplicate_rolls_back_without_updating() -> None:
    """Reject another user's canonical email before persistence."""

    user = make_user()
    session = MagicMock(spec=Session)
    repository = ControlledRepository(user, existing_user=make_user("new@example.com"))

    with pytest.raises(DuplicateEmailError) as exc_info:
        update_current_user_email(
            make_update(),
            user,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == DUPLICATE_EMAIL_MESSAGE
    assert repository.updated_email is None
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize("failure_stage", ["flush", "commit"])
def test_named_unique_race_rolls_back_and_becomes_duplicate(
    failure_stage: str,
) -> None:
    """Translate only uq_users_email failures from either write boundary."""

    user = make_user()
    session = MagicMock(spec=Session)
    failure = make_integrity_error("uq_users_email")
    repository = ControlledRepository(
        user,
        failure=failure if failure_stage == "flush" else None,
    )
    if failure_stage == "commit":
        session.commit.side_effect = failure

    with pytest.raises(DuplicateEmailError) as exc_info:
        update_current_user_email(
            make_update(),
            user,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == DUPLICATE_EMAIL_MESSAGE
    assert "controlled statement" not in str(exc_info.value)
    session.rollback.assert_called_once_with()


def test_unrelated_integrity_error_rolls_back_and_is_reraised() -> None:
    """Never mislabel another database invariant as an email conflict."""

    user = make_user()
    session = MagicMock(spec=Session)
    failure = make_integrity_error("some_other_constraint")
    repository = ControlledRepository(user, failure=failure)

    with pytest.raises(IntegrityError) as exc_info:
        update_current_user_email(
            make_update(),
            user,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()


def test_unexpected_update_failure_rolls_back_and_is_reraised() -> None:
    """Keep operational errors visible while restoring the transaction."""

    user = make_user()
    session = MagicMock(spec=Session)
    failure = RuntimeError("safe controlled failure")
    repository = ControlledRepository(user, failure=failure)

    with pytest.raises(RuntimeError) as exc_info:
        update_current_user_email(
            make_update(),
            user,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
