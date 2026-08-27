"""Real PostgreSQL coverage for the synchronous User repository."""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.repositories.users import UserRepository

pytestmark = pytest.mark.integration


def test_repository_create_flushes_defaults_and_exact_lookup(
    db_session: Session,
) -> None:
    """Load database defaults and match only the exact stored canonical value."""

    email = f"repository-{uuid4()}@example.com"
    password_hash = "$argon2id$integration-test-placeholder"
    repository = UserRepository(db_session)

    assert repository.get_by_email(email) is None
    user = repository.create(email=email, password_hash=password_hash)

    assert user.id is not None
    assert user.created_at is not None
    assert user.updated_at is not None
    assert repository.get_by_email(email) is user
    assert repository.get_by_email(email.upper()) is None
    assert user.email == email
    assert user.password_hash == password_hash

    db_session.rollback()
    assert repository.get_by_email(email) is None
