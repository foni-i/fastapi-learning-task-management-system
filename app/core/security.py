"""Password policy and Argon2id primitives without persistence concerns."""

from pwdlib import PasswordHash

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
PASSWORD_POLICY_ERROR_MESSAGE = (
    "Password must contain 12 to 128 characters and not be only whitespace"
)

_PASSWORD_HASH = PasswordHash.recommended()


class PasswordPolicyError(ValueError):
    """Report a fixed policy failure without retaining the submitted password."""


def validate_password(password: str) -> str:
    """Validate policy while preserving every accepted character exactly."""

    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(PASSWORD_POLICY_ERROR_MESSAGE)
    if password.isspace():
        raise PasswordPolicyError(PASSWORD_POLICY_ERROR_MESSAGE)
    return password


def hash_password(password: str) -> str:
    """Return a salted Argon2id hash for a policy-compliant password."""

    return _PASSWORD_HASH.hash(validate_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password, treating malformed or unsupported hashes as invalid."""

    try:
        return _PASSWORD_HASH.verify(password, password_hash)
    except Exception:  # pwdlib backends can reject malformed hashes differently.
        return False
