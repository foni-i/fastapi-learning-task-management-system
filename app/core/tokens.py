"""Short-lived access JWT creation and validation primitives."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from jwt import PyJWTError

from app.core.config import Settings, get_settings

ACCESS_TOKEN_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
ACCESS_TOKEN_ERROR_MESSAGE = "Access token is invalid"
ACCESS_TOKEN_CONFIGURATION_ERROR_MESSAGE = "Access token signing is not configured"
REQUIRED_ACCESS_TOKEN_CLAIMS = ("sub", "type", "iat", "exp", "iss", "aud")


class AccessTokenError(ValueError):
    """Report all invalid access tokens through one non-sensitive boundary."""


class AccessTokenConfigurationError(RuntimeError):
    """Report missing signing configuration without exposing secret material."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Expose only validated claims needed by authentication consumers."""

    subject: UUID
    issued_at: datetime
    expires_at: datetime


def _get_signing_secret(settings: Settings) -> str:
    secret = settings.access_token_secret
    if secret is None:
        raise AccessTokenConfigurationError(ACCESS_TOKEN_CONFIGURATION_ERROR_MESSAGE)
    return secret.get_secret_value()


def create_access_token(
    user_id: UUID,
    *,
    settings: Settings | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Create one signed access JWT for a user UUID."""

    token_settings = settings if settings is not None else get_settings()
    current_time = issued_at if issued_at is not None else datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("access token issue time must be timezone-aware")
    current_time = current_time.astimezone(UTC)
    expires_at = current_time + timedelta(
        minutes=token_settings.access_token_ttl_minutes
    )
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": current_time,
        "exp": expires_at,
        "iss": token_settings.access_token_issuer,
        "aud": token_settings.access_token_audience,
    }
    return jwt.encode(
        payload,
        _get_signing_secret(token_settings),
        algorithm=ACCESS_TOKEN_ALGORITHM,
    )


def validate_access_token(
    token: str,
    *,
    settings: Settings | None = None,
) -> AccessTokenClaims:
    """Validate every access-token invariant and return minimal typed claims."""

    token_settings = settings if settings is not None else get_settings()
    try:
        payload = jwt.decode(
            token,
            _get_signing_secret(token_settings),
            algorithms=[ACCESS_TOKEN_ALGORITHM],
            issuer=token_settings.access_token_issuer,
            audience=token_settings.access_token_audience,
            options={"require": list(REQUIRED_ACCESS_TOKEN_CLAIMS)},
        )
        if payload["type"] != ACCESS_TOKEN_TYPE:
            raise AccessTokenError(ACCESS_TOKEN_ERROR_MESSAGE)
        subject = UUID(payload["sub"])
        issued_at = datetime.fromtimestamp(payload["iat"], tz=UTC)
        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
    except PyJWTError, KeyError, TypeError, ValueError:
        raise AccessTokenError(ACCESS_TOKEN_ERROR_MESSAGE) from None

    return AccessTokenClaims(
        subject=subject,
        issued_at=issued_at,
        expires_at=expires_at,
    )
