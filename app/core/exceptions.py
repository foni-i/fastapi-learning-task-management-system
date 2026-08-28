"""Safe domain exceptions shared across application boundaries."""

DUPLICATE_EMAIL_MESSAGE = "An account with this email already exists"


class DuplicateEmailError(Exception):
    """Signal a canonical email conflict without carrying persistence details."""
