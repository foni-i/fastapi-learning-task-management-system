"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    )

    app_name: str = "FastAPI STMS"
    env: AppEnvironment = AppEnvironment.DEVELOPMENT
    debug: bool = False
    api_docs_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
