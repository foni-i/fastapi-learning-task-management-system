"""Real PostgreSQL coverage for the registration unique-constraint race."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateEmailError
from app.models import User
from app.repositories.users import UserRepository
from app.schemas.user import UserRegistrationRequest
from app.services.registration import register_user

pytestmark = pytest.mark.integration


class StaleLookupUserRepository(UserRepository):
    """Simulate a pre-check that raced with another committed registration."""

    observed_constraint: str | None = None

    def get_by_email(self, email: str) -> User | None:
        """Return a deliberately stale miss for the concurrency path."""

        return None

    def create(self, *, email: str, password_hash: str) -> User:
        """Record PostgreSQL's named violation before re-raising it."""

        try:
            return super().create(email=email, password_hash=password_hash)
        except IntegrityError as error:
            diagnostic = getattr(error.orig, "diag", None)
            self.observed_constraint = getattr(diagnostic, "constraint_name", None)
            raise


def test_registration_race_translates_named_constraint_and_keeps_one_user(
    db_session: Session,
    integration_engine: Engine,
) -> None:
    """Prove the database final defense survives a deliberately stale pre-check."""

    local_part = f"registration-race-{uuid4()}"
    first_request = UserRegistrationRequest.model_validate(
        {
            "email": f"  {local_part}@EXAMPLE.COM ",
            "password": "first valid password",
        }
    )
    second_request = UserRegistrationRequest.model_validate(
        {
            "email": f"{local_part}@example.com",
            "password": "second valid password",
        }
    )
    assert first_request.email == second_request.email

    first_user = register_user(first_request, db_session)
    repositories: list[StaleLookupUserRepository] = []

    def repository_factory(session: Session) -> UserRepository:
        repository = StaleLookupUserRepository(session)
        repositories.append(repository)
        return repository

    with pytest.raises(DuplicateEmailError):
        register_user(
            second_request,
            db_session,
            repository_factory=repository_factory,
        )

    assert repositories[0].observed_constraint == "uq_users_email"
    remaining_users = db_session.scalar(
        select(func.count()).select_from(User).where(User.email == first_user.email)
    )
    assert remaining_users == 1

    with integration_engine.connect() as independent_connection:
        externally_visible_users = independent_connection.scalar(
            select(func.count()).select_from(User).where(User.email == first_user.email)
        )
    assert externally_visible_users == 0
