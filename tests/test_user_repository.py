"""Unit contract tests for the synchronous User repository."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.models import User
from app.repositories.users import UserRepository


def make_session_double() -> MagicMock:
    """Return a Session-shaped mock whose transaction calls are observable."""

    return MagicMock(spec=Session)


def test_get_by_email_uses_an_exact_user_email_predicate() -> None:
    """Delegate one exact canonical-email select and return its scalar result."""

    session = make_session_double()
    expected_user = User(email="user@example.com", password_hash="stored-hash")
    session.scalar.return_value = expected_user
    repository = UserRepository(session)

    result = repository.get_by_email("user@example.com")

    assert result is expected_user
    statement = session.scalar.call_args.args[0]
    assert isinstance(statement, Select)
    assert statement.column_descriptions[0]["entity"] is User
    assert str(statement.whereclause) == "users.email = :email_1"
    assert statement.compile().params == {"email_1": "user@example.com"}
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_get_by_id_uses_an_exact_user_uuid_predicate() -> None:
    """Resolve only the precise UUID supplied by validated access-token claims."""

    session = make_session_double()
    user_id = uuid4()
    expected_user = User(email="user@example.com", password_hash="stored-hash")
    session.scalar.return_value = expected_user
    repository = UserRepository(session)

    result = repository.get_by_id(user_id)

    assert result is expected_user
    statement = session.scalar.call_args.args[0]
    assert isinstance(statement, Select)
    assert statement.column_descriptions[0]["entity"] is User
    assert str(statement.whereclause) == "users.id = :id_1"
    assert statement.compile().params == {"id_1": user_id}
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_create_adds_and_flushes_only_email_and_hash() -> None:
    """Construct the mapped user without accepting plaintext or ending a transaction."""

    session = make_session_double()
    repository = UserRepository(session)

    user = repository.create(
        email="user@example.com",
        password_hash="argon2id-test-hash",
    )

    assert user.email == "user@example.com"
    assert user.password_hash == "argon2id-test-hash"
    session.add.assert_called_once_with(user)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
