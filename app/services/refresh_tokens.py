"""Internal refresh issuance and atomic credential rotation."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    REFRESH_TOKEN_ISSUANCE_MESSAGE,
    REFRESH_TOKEN_ROTATION_MESSAGE,
    AuthenticationUnavailableError,
    InvalidRefreshTokenError,
    RefreshTokenIssuanceError,
    RefreshTokenRotationError,
)
from app.core.refresh_tokens import (
    INVALID_REFRESH_TOKEN_MESSAGE,
    generate_refresh_token,
    hash_refresh_token,
)
from app.repositories.refresh_tokens import RefreshTokenRepository

REFRESH_TOKEN_TTL = timedelta(days=7)
TokenGenerator = Callable[[], SecretStr]
RepositoryFactory = Callable[[Session], RefreshTokenRepository]


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """An internal delivery value; not a public HTTP response or an ORM row."""

    token: SecretStr = field(repr=False)
    expires_at: datetime


def issue_refresh_token(
    trusted_user_id: UUID,
    session: Session,
    *,
    issued_at: datetime | None = None,
    token_generator: TokenGenerator = generate_refresh_token,
    repository_factory: RepositoryFactory = RefreshTokenRepository,
) -> IssuedRefreshToken:
    """Commit one digest before returning its secret to an authenticated caller.

    The caller owns/closes this use-case Session and authenticates the identity.
    No HTTP client or Agent tool may choose trusted_user_id. This function owns
    commit/rollback and must not be nested in another write use case.
    """

    try:
        result = _prepare_issuance(
            trusted_user_id,
            session,
            issued_at=issued_at,
            token_generator=token_generator,
            repository_factory=repository_factory,
        )
        session.commit()
        return result
    except Exception:
        # Even rollback diagnostics may contain SQL parameters. The caller
        # must discard this failed Session; never retry issuance internally.
        with suppress(Exception):
            session.rollback()
        raise RefreshTokenIssuanceError(REFRESH_TOKEN_ISSUANCE_MESSAGE) from None


@dataclass(frozen=True, slots=True)
class RotatedRefreshToken:
    """Internal delivery with an identity derived only from the old credential."""

    user_id: UUID
    token: SecretStr = field(repr=False)
    expires_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def logout_refresh_token(
    token: SecretStr,
    session: Session,
    *,
    clock: Callable[[], datetime] = _utc_now,
    repository_factory: RepositoryFactory = RefreshTokenRepository,
) -> None:
    """Idempotently revoke the presented credential in a dedicated transaction.

    Possession identifies the row; clients cannot select its owner. No successor,
    other session or access JWT is revoked. The caller owns Session lifetime.
    """
    try:
        repository = repository_factory(session)
        record = repository.get_by_hash_for_update(hash_refresh_token(token))
        if record is not None and record.revoked_at is None:
            now = clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("Logout time must be timezone-aware")
            # A clock rollback must not prevent revocation or break the DB check.
            revoked_at = max(now.astimezone(UTC), record.created_at)
            if not repository.revoke_if_unrevoked(
                token_id=record.id, user_id=record.user_id, revoked_at=revoked_at
            ):
                raise RuntimeError("Locked credential could not be revoked")
        session.commit()
    except Exception:
        with suppress(Exception):
            session.rollback()
        raise AuthenticationUnavailableError(
            AUTHENTICATION_UNAVAILABLE_MESSAGE
        ) from None


def rotate_refresh_token(
    token: SecretStr,
    session: Session,
    *,
    clock: Callable[[], datetime] = _utc_now,
    token_generator: TokenGenerator = generate_refresh_token,
    repository_factory: RepositoryFactory = RefreshTokenRepository,
) -> RotatedRefreshToken:
    """Consume one credential and issue its replacement in one transaction.

    Uses a dedicated caller-owned Session; never nest this committing use case
    inside another write transaction. Old rows remain for reuse rejection.
    """

    try:
        result = _prepare_rotation(
            token,
            session,
            clock=clock,
            token_generator=token_generator,
            repository_factory=repository_factory,
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
        raise RefreshTokenRotationError(REFRESH_TOKEN_ROTATION_MESSAGE) from None


def _prepare_issuance(
    trusted_user_id: UUID,
    session: Session,
    *,
    issued_at: datetime | None = None,
    token_generator: TokenGenerator = generate_refresh_token,
    repository_factory: RepositoryFactory = RefreshTokenRepository,
) -> IssuedRefreshToken:
    """Prepare a digest in the caller's transaction; never deliver before commit."""
    created_at = issued_at if issued_at is not None else datetime.now(UTC)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("Refresh token issue time must be timezone-aware")
    created_at = created_at.astimezone(UTC)
    token = token_generator()
    token_hash = hash_refresh_token(token)
    expires_at = created_at + REFRESH_TOKEN_TTL
    result = IssuedRefreshToken(token=token, expires_at=expires_at)
    repository = repository_factory(session)
    repository.create(
        user_id=trusted_user_id,
        token_hash=token_hash,
        created_at=created_at,
        expires_at=expires_at,
    )
    return result


def _prepare_rotation(
    token: SecretStr,
    session: Session,
    *,
    clock: Callable[[], datetime] = _utc_now,
    token_generator: TokenGenerator = generate_refresh_token,
    repository_factory: RepositoryFactory = RefreshTokenRepository,
) -> RotatedRefreshToken:
    """Prepare rotation in the caller's transaction without committing."""
    try:
        old_hash = hash_refresh_token(token)
    except ValueError:
        raise InvalidRefreshTokenError(INVALID_REFRESH_TOKEN_MESSAGE) from None
    repository = repository_factory(session)
    record = repository.get_by_hash_for_update(old_hash)
    if record is None:
        raise InvalidRefreshTokenError(INVALID_REFRESH_TOKEN_MESSAGE)
    # Sample after lock acquisition, not before waiting for a competitor.
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Refresh token rotation time must be timezone-aware")
    now = now.astimezone(UTC)
    if (
        record.revoked_at is not None
        or not record.created_at <= now < record.expires_at
    ):
        raise InvalidRefreshTokenError(INVALID_REFRESH_TOKEN_MESSAGE)
    if not repository.revoke_if_active(
        token_id=record.id, user_id=record.user_id, revoked_at=now
    ):
        raise InvalidRefreshTokenError(INVALID_REFRESH_TOKEN_MESSAGE)
    replacement = token_generator()
    replacement_hash = hash_refresh_token(replacement)
    expires_at = now + REFRESH_TOKEN_TTL
    result = RotatedRefreshToken(record.user_id, replacement, expires_at)
    repository.create(
        user_id=record.user_id,
        token_hash=replacement_hash,
        created_at=now,
        expires_at=expires_at,
    )
    return result
