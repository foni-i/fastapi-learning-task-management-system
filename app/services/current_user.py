"""Authenticated current-user update use cases without HTTP concerns."""

from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.models.user import User
from app.repositories.users import UserRepository
from app.schemas.user import CurrentUserEmailUpdate, PublicUser

RepositoryFactory = Callable[[Session], UserRepository]
USER_EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


def _is_user_email_unique_violation(error: IntegrityError) -> bool:
    """Identify only PostgreSQL's canonical-email named unique constraint."""

    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == USER_EMAIL_UNIQUE_CONSTRAINT


def update_current_user_email(
    update: CurrentUserEmailUpdate,
    current_user: User,
    session: Session,
    *,
    repository_factory: RepositoryFactory = UserRepository,
) -> PublicUser:
    """Update only the authenticated user's canonical email in one transaction."""

    if update.email == current_user.email:
        return PublicUser.model_validate(current_user)

    try:
        repository = repository_factory(session)
        existing_user = repository.get_by_email(update.email)
        if existing_user is not None and existing_user.id != current_user.id:
            raise DuplicateEmailError(DUPLICATE_EMAIL_MESSAGE)

        updated_user = repository.update_email(current_user, email=update.email)
        public_user = PublicUser.model_validate(updated_user)
        session.commit()
        return public_user
    except IntegrityError as error:
        session.rollback()
        if _is_user_email_unique_violation(error):
            raise DuplicateEmailError(DUPLICATE_EMAIL_MESSAGE) from None
        raise
    except Exception:
        session.rollback()
        raise
