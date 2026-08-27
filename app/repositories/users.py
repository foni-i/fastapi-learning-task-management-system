"""User persistence operations with caller-owned transaction control."""

from sqlalchemy import select
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

    def create(self, *, email: str, password_hash: str) -> User:
        """Add and flush a user without committing the caller's transaction."""

        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        self._session.flush()
        return user
