"""User persistence operations with caller-owned transaction control."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Persist users through a caller-owned synchronous Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, email: str) -> User | None:
        """Return the user whose stored canonical email exactly matches."""

        statement = select(User).where(User.email == email)
        return self._session.scalar(statement)

    def get_by_id(self, user_id: UUID) -> User | None:
        """Return the user whose UUID exactly matches the validated subject."""

        statement = select(User).where(User.id == user_id)
        return self._session.scalar(statement)

    def get_by_email_for_update(self, email: str) -> User | None:
        """Serialize login with password changes before checking the password."""
        self._session.execute(text("SET LOCAL lock_timeout = '5s'"))
        return self._session.scalar(
            select(User)
            .where(User.email == email)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_by_id_for_update(self, user_id: UUID) -> User | None:
        """Lock the trusted owner before any refresh-row locks or inserts."""
        self._session.execute(text("SET LOCAL lock_timeout = '5s'"))
        return self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def update_password(
        self, user: User, *, password_hash: str, updated_at: datetime
    ) -> None:
        user.password_hash = password_hash
        user.updated_at = updated_at
        self._session.flush()

    def create(self, *, email: str, password_hash: str) -> User:
        """Add and flush a user without committing the caller's transaction."""

        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        self._session.flush()
        return user

    def update_email(self, user: User, *, email: str) -> User:
        """Change only a tracked user's canonical email and flush the caller's work."""

        user.email = email
        self._session.flush()
        return user
