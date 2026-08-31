"""Authentication request and access-token response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.email_normalization import normalize_email

LOGIN_PASSWORD_ERROR_MESSAGE = "Password must contain between 1 and 128 characters"


class UserLoginRequest(BaseModel):
    """Accept canonical email and an unchanged secret-aware login password."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email_value(cls, value: str) -> str:
        """Reuse the canonical email representation used during registration."""

        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password_value(cls, value: SecretStr) -> SecretStr:
        """Bound login work without changing or exposing the supplied password."""

        length = len(value.get_secret_value())
        if length < 1 or length > 128:
            raise ValueError(LOGIN_PASSWORD_ERROR_MESSAGE)
        return value


class AccessTokenResponse(BaseModel):
    """Expose only the bearer access token contract."""

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"


class AuthenticationErrorResponse(BaseModel):
    """Document the fixed public authentication failure shape."""

    model_config = ConfigDict(extra="forbid")

    detail: str
