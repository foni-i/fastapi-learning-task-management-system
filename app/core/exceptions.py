"""Safe domain exceptions shared across application boundaries."""

DUPLICATE_EMAIL_MESSAGE = "An account with this email already exists"
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"


class DuplicateEmailError(Exception):
    """Signal a canonical email conflict without carrying persistence details."""


class InvalidCredentialsError(Exception):
    """Signal authentication failure without revealing which credential failed."""
