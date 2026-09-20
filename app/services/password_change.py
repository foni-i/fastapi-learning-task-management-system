"""Current-password reauthentication and atomic all-refresh revocation."""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    AuthenticationUnavailableError,
    InvalidCredentialsError,
)
from app.core.security import hash_password, verify_password
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.user import PasswordChangeRequest


def change_password(
    credentials: PasswordChangeRequest,
    authenticated_user_id: UUID,
    session: Session,
    *,
    password_verifier: Callable[[str, str], bool] = verify_password,
    password_hasher: Callable[[str], str] = hash_password,
) -> None:
    """Own one transaction; never trust a previously loaded password snapshot."""
    try:
        users = UserRepository(session)
        user = users.get_by_id_for_update(authenticated_user_id)
        if user is None or not password_verifier(
            credentials.current_password.get_secret_value(), user.password_hash
        ):
            raise InvalidCredentialsError(AUTHENTICATION_REQUIRED_MESSAGE)
        replacement = password_hasher(credentials.new_password.get_secret_value())
        now = datetime.now(UTC)
        users.update_password(user, password_hash=replacement, updated_at=now)
        RefreshTokenRepository(session).revoke_all_for_owner(user.id, revoked_at=now)
        session.commit()
    except InvalidCredentialsError:
        with suppress(Exception):
            session.rollback()
        raise InvalidCredentialsError(AUTHENTICATION_REQUIRED_MESSAGE) from None
    except Exception:
        with suppress(Exception):
            session.rollback()
        raise AuthenticationUnavailableError(
            AUTHENTICATION_UNAVAILABLE_MESSAGE
        ) from None
