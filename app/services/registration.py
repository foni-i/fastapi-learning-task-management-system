"""Successful user-registration orchestration without HTTP concerns."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.email_normalization import normalize_email
from app.core.security import hash_password, validate_password
from app.repositories.users import UserRepository
from app.schemas.user import PublicUser, UserRegistrationRequest

EmailNormalizer = Callable[[str], str]
PasswordPolicy = Callable[[str], str]
PasswordHasher = Callable[[str], str]
RepositoryFactory = Callable[[Session], UserRepository]


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
        accepted_password = password_policy(plaintext)
        password_hash = password_hasher(accepted_password)
        repository = repository_factory(session)
        user = repository.create(
            email=canonical_email,
            password_hash=password_hash,
        )
        public_user = PublicUser.model_validate(user)
        session.commit()
        return public_user
    except Exception:
        session.rollback()
        raise
    finally:
        del plaintext
