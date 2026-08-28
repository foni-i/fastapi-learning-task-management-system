"""Unit tests for registration orchestration and transaction ownership."""

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import NoReturn, cast
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.repositories.users import UserRepository
from app.schemas.user import PublicUser, UserRegistrationRequest
from app.services.registration import register_user


class ControlledRepository:
    """Record safe persistence inputs and optionally fail creation."""

    def __init__(
        self,
        events: list[str],
        user: SimpleNamespace,
        failure: Exception | None = None,
        existing_user: SimpleNamespace | None = None,
    ) -> None:
        self.events = events
        self.user = user
        self.failure = failure
        self.existing_user = existing_user
        self.received_email: str | None = None
        self.received_hash: str | None = None

    def get_by_email(self, email: str) -> SimpleNamespace | None:
        """Record the exact lookup that must precede hashing."""

        self.events.append("repository.get_by_email")
        self.received_email = email
        return self.existing_user

    def create(self, *, email: str, password_hash: str) -> SimpleNamespace:
        """Capture only canonical email and non-plaintext hash material."""

        self.events.append("repository.create")
        self.received_email = email
        self.received_hash = password_hash
        if self.failure is not None:
            raise self.failure
        self.user.email = email
        self.user.password_hash = password_hash
        return self.user


class ControlledDatabaseError(Exception):
    """Provide the minimal Psycopg diagnostic shape expected by the service."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__("controlled database failure")
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def make_registration() -> UserRegistrationRequest:
    """Build validated input without exposing its secret in test IDs or output."""

    return UserRegistrationRequest.model_validate(
        {"email": "User@EXAMPLE.COM", "password": "valid password value"}
    )


def make_user() -> SimpleNamespace:
    """Return public ORM-like attributes plus an internal hash attribute."""

    timestamp = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        password_hash="not-yet-set",
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_registration_coordinates_security_persistence_and_commit_in_order() -> None:
    """Pass exact plaintext only through policy/hasher and return a public result."""

    events: list[str] = []
    registration = make_registration()
    expected_plaintext = registration.password.get_secret_value()
    generated_hash = "$argon2id$controlled-hash"
    session = MagicMock(spec=Session)
    user = make_user()
    repository = ControlledRepository(events, user)

    def normalize(email: str) -> str:
        events.append("normalize")
        assert email == "user@example.com"
        return email

    def enforce(password: str) -> str:
        events.append("policy")
        assert password == expected_plaintext
        return password

    def hash_secret(password: str) -> str:
        events.append("hash")
        assert password == expected_plaintext
        return generated_hash

    def make_repository(received_session: Session) -> UserRepository:
        events.append("repository.factory")
        assert received_session is session
        return cast(UserRepository, repository)

    session.commit.side_effect = lambda: events.append("commit")
    result = register_user(
        registration,
        session,
        email_normalizer=normalize,
        password_policy=enforce,
        password_hasher=hash_secret,
        repository_factory=make_repository,
    )

    assert events == [
        "normalize",
        "repository.factory",
        "repository.get_by_email",
        "policy",
        "hash",
        "repository.create",
        "commit",
    ]
    assert repository.received_email == "user@example.com"
    assert repository.received_hash == generated_hash
    assert isinstance(result, PublicUser)
    assert set(result.model_dump()) == {"id", "email", "created_at", "updated_at"}
    assert "password" not in result.model_dump_json()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["hash", "repository", "commit"])
def test_registration_rolls_back_once_and_reraises_failures(
    failure_stage: str,
) -> None:
    """Keep the service transaction atomic without translating future conflicts."""

    registration = make_registration()
    session = MagicMock(spec=Session)
    failure = RuntimeError("safe controlled failure")
    repository = ControlledRepository(
        [],
        make_user(),
        failure if failure_stage == "repository" else None,
    )

    def fail_hash(_password: str) -> NoReturn:
        raise failure

    hasher: Callable[[str], str] = (
        fail_hash if failure_stage == "hash" else lambda _password: "stored-hash"
    )
    if failure_stage == "commit":
        session.commit.side_effect = failure

    with pytest.raises(RuntimeError) as exc_info:
        register_user(
            registration,
            session,
            password_hasher=hasher,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    if failure_stage == "hash":
        assert repository.received_hash is None
    if failure_stage != "commit":
        session.commit.assert_not_called()


def test_repository_never_receives_the_plaintext_password() -> None:
    """Prove persistence sees only the hasher result and public output omits it."""

    registration = make_registration()
    session = MagicMock(spec=Session)
    repository = ControlledRepository([], make_user())
    generated_hash = "$argon2id$another-controlled-hash"

    result = register_user(
        registration,
        session,
        password_hasher=lambda _password: generated_hash,
        repository_factory=lambda _session: cast(UserRepository, repository),
    )

    assert repository.received_hash == generated_hash
    assert result.model_dump().keys() == {"id", "email", "created_at", "updated_at"}
    assert generated_hash not in result.model_dump_json()


def make_integrity_error(constraint_name: str) -> IntegrityError:
    """Build a driver-shaped error without exposing its diagnostics to callers."""

    original = ControlledDatabaseError(constraint_name)
    return IntegrityError("controlled statement", {}, original)


def test_early_duplicate_is_detected_before_hashing_and_rolled_back() -> None:
    """Avoid expensive secret work while preserving service transaction ownership."""

    events: list[str] = []
    registration = make_registration()
    session = MagicMock(spec=Session)
    hasher = Mock()
    repository = ControlledRepository(
        events,
        make_user(),
        existing_user=make_user(),
    )

    with pytest.raises(DuplicateEmailError) as exc_info:
        register_user(
            registration,
            session,
            password_hasher=hasher,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == DUPLICATE_EMAIL_MESSAGE
    assert events == ["repository.get_by_email"]
    hasher.assert_not_called()
    assert repository.received_hash is None
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize("failure_stage", ["flush", "commit"])
def test_named_email_constraint_is_translated_after_rollback(
    failure_stage: str,
) -> None:
    """Map only the named final database defense to the duplicate domain error."""

    registration = make_registration()
    session = MagicMock(spec=Session)
    failure = make_integrity_error("uq_users_email")
    repository = ControlledRepository(
        [],
        make_user(),
        failure if failure_stage == "flush" else None,
    )
    if failure_stage == "commit":
        session.commit.side_effect = failure

    with pytest.raises(DuplicateEmailError) as exc_info:
        register_user(
            registration,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert str(exc_info.value) == DUPLICATE_EMAIL_MESSAGE
    assert "controlled statement" not in str(exc_info.value)
    session.rollback.assert_called_once_with()


def test_unrelated_integrity_error_is_rolled_back_and_reraised() -> None:
    """Never mislabel another database invariant as a duplicate email."""

    registration = make_registration()
    session = MagicMock(spec=Session)
    failure = make_integrity_error("some_other_constraint")
    repository = ControlledRepository([], make_user(), failure)

    with pytest.raises(IntegrityError) as exc_info:
        register_user(
            registration,
            session,
            repository_factory=lambda _session: cast(UserRepository, repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
