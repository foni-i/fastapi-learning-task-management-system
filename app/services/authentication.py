"""Credential authentication and access-token issuance without HTTP concerns."""

from collections.abc import Callable
from contextlib import suppress
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.email_normalization import normalize_email
from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    AuthenticationUnavailableError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.refresh_tokens import INVALID_REFRESH_TOKEN_MESSAGE
from app.core.security import verify_password
from app.core.tokens import create_access_token
from app.repositories.users import UserRepository
from app.schemas.auth import TokenPairResponse, UserLoginRequest
from app.services.refresh_tokens import _prepare_issuance, _prepare_rotation

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
) -> TokenPairResponse:
    """Authenticate and prepare both credentials before committing one transaction."""

    plaintext = credentials.password.get_secret_value()
    try:
        canonical_email = email_normalizer(credentials.email)
        repository = repository_factory(session)
        user = repository.get_by_email_for_update(canonical_email)
        if user is None or not password_verifier(plaintext, user.password_hash):
            raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

        refresh = _prepare_issuance(user.id, session)
        result = TokenPairResponse(
            access_token=token_issuer(user.id),
            refresh_token=refresh.token.get_secret_value(),
            refresh_expires_at=refresh.expires_at,
        )
        session.commit()
        return result
    except InvalidCredentialsError:
        with suppress(Exception):
            session.rollback()
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE) from None
    except Exception:
        with suppress(Exception):
            session.rollback()
        raise AuthenticationUnavailableError(
            AUTHENTICATION_UNAVAILABLE_MESSAGE
        ) from None
    finally:
        del plaintext


def refresh_authentication(
    token: SecretStr,
    session: Session,
    *,
    token_issuer: TokenIssuer = create_access_token,
) -> TokenPairResponse:
    """Rotate and sign using only the locked credential's owner before commit."""

    try:
        refresh = _prepare_rotation(token, session)
        result = TokenPairResponse(
            access_token=token_issuer(refresh.user_id),
            refresh_token=refresh.token.get_secret_value(),
            refresh_expires_at=refresh.expires_at,
        )
        session.commit()
        return result
    except InvalidRefreshTokenError:
        with suppress(Exception):
            session.rollback()
        raise InvalidRefreshTokenError(INVALID_REFRESH_TOKEN_MESSAGE) from None
    except Exception:
        with suppress(Exception):
            session.rollback()
        raise AuthenticationUnavailableError(
            AUTHENTICATION_UNAVAILABLE_MESSAGE
        ) from None
