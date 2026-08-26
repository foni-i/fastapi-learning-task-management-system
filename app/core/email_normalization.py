"""Deterministic email validation and normalization."""

from email_validator import EmailNotValidError, validate_email

MAX_EMAIL_LENGTH = 254
INVALID_EMAIL_MESSAGE = "Email address is invalid"


class EmailNormalizationError(ValueError):
    """Report email validation failures without echoing the submitted value."""


def normalize_email(raw_email: str) -> str:
    """Return the one canonical email value stored by the application."""

    stripped_email = raw_email.strip()
    if not stripped_email:
        raise EmailNormalizationError(INVALID_EMAIL_MESSAGE)

    try:
        validated_email = validate_email(
            stripped_email,
            check_deliverability=False,
        )
    except EmailNotValidError:
        raise EmailNormalizationError(INVALID_EMAIL_MESSAGE) from None

    normalized_email = validated_email.normalized.casefold()
    if len(normalized_email) > MAX_EMAIL_LENGTH:
        raise EmailNormalizationError(INVALID_EMAIL_MESSAGE)

    return normalized_email
