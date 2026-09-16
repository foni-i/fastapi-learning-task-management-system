"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

POSTGRESQL_DRIVER = "postgresql+psycopg"
MIN_ACCESS_TOKEN_SECRET_LENGTH = 32
MIN_ACCESS_TOKEN_TTL_MINUTES = 1
MAX_ACCESS_TOKEN_TTL_MINUTES = 60
MAX_MODEL_PROVIDER_LENGTH = 100
MAX_MODEL_NAME_LENGTH = 200
MIN_EMBEDDING_TIMEOUT_SECONDS = 0.1
MAX_EMBEDDING_TIMEOUT_SECONDS = 120.0


class AppEnvironment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings with safe defaults for local development and tests."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="STMS_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "FastAPI STMS"
    env: AppEnvironment = AppEnvironment.DEVELOPMENT
    debug: bool = False
    api_docs_enabled: bool = True
    database_url: SecretStr | None = None
    access_token_secret: SecretStr | None = None
    access_token_ttl_minutes: int = 15
    access_token_issuer: str = "fastapi-stms"
    access_token_audience: str = "fastapi-stms-api"
    model_provider: str | None = None
    model_name: str | None = None
    embedding_model: str | None = None
    embedding_timeout_seconds: float = 30.0
    model_api_key: SecretStr | None = None

    @field_validator("model_provider")
    @classmethod
    def validate_model_provider(cls, value: str | None) -> str | None:
        """Bound a configured provider identifier without accepting blanks."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("model provider must not be blank")
        if len(normalized) > MAX_MODEL_PROVIDER_LENGTH:
            raise ValueError("model provider is too long")
        return normalized

    @field_validator("model_name", "embedding_model")
    @classmethod
    def validate_model_name(cls, value: str | None) -> str | None:
        """Bound a configured model identifier without accepting blanks."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("model name must not be blank")
        if len(normalized) > MAX_MODEL_NAME_LENGTH:
            raise ValueError("model name is too long")
        return normalized

    @field_validator("embedding_timeout_seconds")
    @classmethod
    def validate_embedding_timeout(cls, value: float) -> float:
        """Keep synchronous embedding calls within one bounded request window."""

        if not MIN_EMBEDDING_TIMEOUT_SECONDS <= value <= MAX_EMBEDDING_TIMEOUT_SECONDS:
            raise ValueError("embedding timeout must be between 0.1 and 120 seconds")
        return value

    @field_validator("model_api_key")
    @classmethod
    def validate_model_api_key(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        """Reject blank provider credentials without disclosing their content."""

        if value is not None and not value.get_secret_value().strip():
            raise ValueError("model API key must not be blank")
        return value

    @field_validator("access_token_secret")
    @classmethod
    def validate_access_token_secret(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        """Require strong configured signing material without exposing it."""

        if (
            value is not None
            and len(value.get_secret_value()) < MIN_ACCESS_TOKEN_SECRET_LENGTH
        ):
            raise ValueError("access token secret must contain at least 32 characters")
        return value

    @field_validator("access_token_ttl_minutes")
    @classmethod
    def validate_access_token_ttl(cls, value: int) -> int:
        """Keep short-lived access tokens within the accepted Stage 4 bound."""

        if not MIN_ACCESS_TOKEN_TTL_MINUTES <= value <= MAX_ACCESS_TOKEN_TTL_MINUTES:
            raise ValueError("access token TTL must be between 1 and 60 minutes")
        return value

    @field_validator("access_token_issuer", "access_token_audience")
    @classmethod
    def validate_access_token_identity(cls, value: str) -> str:
        """Reject empty issuer or audience values without silently rewriting them."""

        if not value.strip():
            raise ValueError("access token issuer and audience must not be blank")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        """Accept complete synchronous Psycopg URLs without exposing secrets."""

        if value is None:
            return None

        raw_url = value.get_secret_value()
        if not raw_url.startswith(f"{POSTGRESQL_DRIVER}://"):
            raise ValueError("database URL must use the postgresql+psycopg driver")

        try:
            parsed_url = make_url(raw_url)
            parsed_port = parsed_url.port
        except ArgumentError, ValueError:
            raise ValueError("database URL must be structurally valid") from None

        if parsed_port is not None and not 1 <= parsed_port <= 65535:
            raise ValueError("database URL port must be between 1 and 65535")
        if parsed_url.drivername != POSTGRESQL_DRIVER:
            raise ValueError("database URL must use the postgresql+psycopg driver")
        if not parsed_url.host or not parsed_url.database:
            raise ValueError("database URL must include a host and database name")

        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
