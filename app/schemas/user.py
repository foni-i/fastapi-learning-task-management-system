"""Registration input and public user output contracts."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from app.core.email_normalization import normalize_email
from app.core.security import validate_password

TIMEZONE_ERROR_MESSAGE = "Timestamp must include timezone information"


class UserRegistrationRequest(BaseModel):
    """Accept only canonicalizable email and secret-aware password input."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email_value(cls, value: str) -> str:
        """Store the shared canonical email representation in the schema."""

        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password_value(cls, value: SecretStr) -> SecretStr:
        """Enforce the shared policy without replacing or exposing the secret."""

        validate_password(value.get_secret_value())
        return value


class PublicUser(BaseModel):
    """Expose the explicit public allowlist for a persisted user."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        hide_input_in_errors=True,
    )

    id: UUID
    email: str
    created_at: datetime
    updated_at: datetime

    @field_validator("email")
    @classmethod
    def normalize_email_value(cls, value: str) -> str:
        """Return the same canonical email representation used for storage."""

        return normalize_email(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject naive values and normalize aware public timestamps to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(TIMEZONE_ERROR_MESSAGE)

        return value.astimezone(UTC)
