"""Credential authentication and access-token issuance without HTTP concerns."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.email_normalization import normalize_email
from app.core.exceptions import INVALID_CREDENTIALS_MESSAGE, InvalidCredentialsError
from app.core.security import verify_password
from app.core.tokens import create_access_token
from app.repositories.users import UserRepository
from app.schemas.auth import AccessTokenResponse, UserLoginRequest

EmailNormalizer = Callable[[str], str]
PasswordVerifier = Callable[[str, str], bool]
TokenIssuer = Callable[[UUID], str]
RepositoryFactory = Callable[[Session], UserRepository]


def authenticate_user(
    credentials: UserLoginRequest,
    session: Session,
    *,
    email_normalizer: EmailNormalizer = normalize_email,
    password_verifier: PasswordVerifier = verify_password,
    token_issuer: TokenIssuer = create_access_token,
    repository_factory: RepositoryFactory = UserRepository,
) -> AccessTokenResponse:
    """Authenticate one user and issue an access token without a write transaction."""

    plaintext = credentials.password.get_secret_value()
    try:
        canonical_email = email_normalizer(credentials.email)
        repository = repository_factory(session)
        user = repository.get_by_email(canonical_email)
        if user is None or not password_verifier(plaintext, user.password_hash):
            raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

        return AccessTokenResponse(access_token=token_issuer(user.id))
    finally:
        del plaintext
