"""Opaque refresh-token generation and bounded digest primitives."""

import hashlib
import re
import secrets

from pydantic import SecretStr

REFRESH_TOKEN_RANDOM_BYTES = 32
REFRESH_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
INVALID_REFRESH_TOKEN_MESSAGE = "Refresh token is invalid"


def generate_refresh_token() -> SecretStr:
    """Generate an opaque credential with an explicit 256-bit random input."""

    return SecretStr(secrets.token_urlsafe(REFRESH_TOKEN_RANDOM_BYTES))


def hash_refresh_token(token: SecretStr) -> str:
    """Digest exact token bytes without trimming or case normalization."""

    raw = token.get_secret_value()
    if REFRESH_TOKEN_PATTERN.fullmatch(raw) is None:
        raise ValueError(INVALID_REFRESH_TOKEN_MESSAGE)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
