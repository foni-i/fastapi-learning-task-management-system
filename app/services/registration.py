"""Successful user-registration orchestration without HTTP concerns."""

from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.email_normalization import normalize_email
from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.core.security import hash_password, validate_password
from app.repositories.users import UserRepository
from app.schemas.user import PublicUser, UserRegistrationRequest

EmailNormalizer = Callable[[str], str]
PasswordPolicy = Callable[[str], str]
PasswordHasher = Callable[[str], str]
RepositoryFactory = Callable[[Session], UserRepository]
USER_EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


def _is_user_email_unique_violation(error: IntegrityError) -> bool:
    """Identify only PostgreSQL's named canonical-email unique violation."""

    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == USER_EMAIL_UNIQUE_CONSTRAINT


def register_user(
    registration: UserRegistrationRequest,
    session: Session,
    *,
    email_normalizer: EmailNormalizer = normalize_email,
    password_policy: PasswordPolicy = validate_password,
    password_hasher: PasswordHasher = hash_password,
    repository_factory: RepositoryFactory = UserRepository,
) -> PublicUser:
    """Create one user and own the complete successful write transaction."""

    plaintext = registration.password.get_secret_value()
    try:
        canonical_email = email_normalizer(registration.email)
        repository = repository_factory(session)
        if repository.get_by_email(canonical_email) is not None:
            raise DuplicateEmailError(DUPLICATE_EMAIL_MESSAGE)
        accepted_password = password_policy(plaintext)
        password_hash = password_hasher(accepted_password)
        user = repository.create(
            email=canonical_email,
            password_hash=password_hash,
        )
        public_user = PublicUser.model_validate(user)
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
    finally:
        del plaintext
