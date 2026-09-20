"""Authentication request and access-token response contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.email_normalization import normalize_email
from app.core.refresh_tokens import REFRESH_TOKEN_PATTERN

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


class RefreshTokenRequest(BaseModel):
    """Accept exactly one opaque credential, never a caller-chosen identity."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    refresh_token: SecretStr = Field(min_length=43, max_length=43)

    @field_validator("refresh_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if REFRESH_TOKEN_PATTERN.fullmatch(value.get_secret_value()) is None:
            raise ValueError("Invalid refresh token format")
        return value


class TokenPairResponse(AccessTokenResponse):
    """Explicit credential delivery only; ordinary repr hides both tokens."""

    refresh_token: str = Field(repr=False, min_length=43, max_length=43)
    refresh_expires_at: datetime


class AuthenticationValidationIssue(BaseModel):
    """Only server-selected locations and fixed messages cross this boundary."""

    loc: list[str]
    type: Literal["invalid_request"] = "invalid_request"
    msg: Literal["Invalid authentication request"] = "Invalid authentication request"


class AuthenticationValidationResponse(BaseModel):
    detail: list[AuthenticationValidationIssue]
