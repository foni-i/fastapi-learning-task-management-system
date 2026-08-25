"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

POSTGRESQL_DRIVER = "postgresql+psycopg"


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
