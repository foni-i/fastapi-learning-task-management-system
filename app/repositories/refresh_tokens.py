"""Hash-only refresh persistence and locking with caller-owned transactions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models import RefreshToken, User
from app.repositories.users import UserRepository


class RefreshTokenRepository:
    """Accept storage metadata, never a plaintext refresh credential."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> RefreshToken:
        # All credential creation shares the password-change serialization lock.
        if UserRepository(self._session).get_by_id_for_update(user_id) is None:
            raise ValueError("Credential owner no longer exists")
        record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        """Authenticate opaque possession before an owner identity is available.

        This credential lookup is not a caller-selected resource lookup. All
        subsequent writes use the owner loaded from this locked record.
        """

        self._session.execute(text("SET LOCAL lock_timeout = '5s'"))
        owner = self._session.scalar(
            select(User.id)
            .where(
                User.id
                == select(RefreshToken.user_id)
                .where(RefreshToken.token_hash == token_hash)
                .scalar_subquery()
            )
            .with_for_update()
        )
        if owner is None:
            return None
        statement = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.user_id == owner)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self._session.scalar(statement)

    def revoke_if_active(
        self, *, token_id: UUID, user_id: UUID, revoked_at: datetime
    ) -> bool:
        """Consume the locked credential, retaining owner and lifetime predicates."""

        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.id == token_id,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > revoked_at,
                RefreshToken.created_at <= revoked_at,
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return self._session.scalar(statement) is not None

    def revoke_all_for_owner(self, user_id: UUID, *, revoked_at: datetime) -> None:
        """Caller must hold the user row lock before this bulk revocation."""
        self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.greatest(revoked_at, RefreshToken.created_at))
            .execution_options(synchronize_session=False)
        )

    def revoke_if_unrevoked(
        self, *, token_id: UUID, user_id: UUID, revoked_at: datetime
    ) -> bool:
        """Revoke only the locked credential, including an expired credential."""
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.id == token_id,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return self._session.scalar(statement) is not None
