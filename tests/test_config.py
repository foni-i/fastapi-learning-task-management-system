"""Tests for typed application settings."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import AppEnvironment, Settings, get_settings
from app.main import create_app

DATABASE_PASSWORD = "test-only-password"
VALID_DATABASE_URL = (
    f"postgresql+psycopg://test_user:{DATABASE_PASSWORD}@127.0.0.1:5432/test_database"
)
SETTINGS_ENVIRONMENT_VARIABLES = (
    "STMS_APP_NAME",
    "STMS_ENV",
    "STMS_DEBUG",
    "STMS_API_DOCS_ENABLED",
    "STMS_DATABASE_URL",
    "STMS_ACCESS_TOKEN_SECRET",
    "STMS_ACCESS_TOKEN_TTL_MINUTES",
    "STMS_ACCESS_TOKEN_ISSUER",
    "STMS_ACCESS_TOKEN_AUDIENCE",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Isolate environment-backed settings and their process-wide cache."""

    monkeypatch.chdir(tmp_path)
    for variable_name in SETTINGS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)
    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


def test_settings_use_safe_defaults_without_env_file() -> None:
    """A missing optional .env file must not prevent settings from loading."""

    settings = Settings()

    assert settings.app_name == "FastAPI STMS"
    assert settings.env is AppEnvironment.DEVELOPMENT
    assert settings.debug is False
    assert settings.api_docs_enabled is True
    assert settings.database_url is None
    assert settings.access_token_secret is None
    assert settings.access_token_ttl_minutes == 15
    assert settings.access_token_issuer == "fastapi-stms"
    assert settings.access_token_audience == "fastapi-stms-api"


def test_settings_load_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STMS-prefixed environment variables override the defaults with typed values."""

    monkeypatch.setenv("STMS_APP_NAME", "Configured STMS")
    monkeypatch.setenv("STMS_ENV", "test")
    monkeypatch.setenv("STMS_DEBUG", "true")
    monkeypatch.setenv("STMS_API_DOCS_ENABLED", "false")
    monkeypatch.setenv("STMS_DATABASE_URL", VALID_DATABASE_URL)

    settings = get_settings()

    assert settings.app_name == "Configured STMS"
    assert settings.env is AppEnvironment.TEST
    assert settings.debug is True
    assert settings.api_docs_enabled is False
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == VALID_DATABASE_URL


def test_access_token_settings_load_secure_typed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load secret-aware signing settings through the established prefix."""

    secret = "synthetic-access-token-secret-value"
    monkeypatch.setenv("STMS_ACCESS_TOKEN_SECRET", secret)
    monkeypatch.setenv("STMS_ACCESS_TOKEN_TTL_MINUTES", "30")
    monkeypatch.setenv("STMS_ACCESS_TOKEN_ISSUER", "configured-issuer")
    monkeypatch.setenv("STMS_ACCESS_TOKEN_AUDIENCE", "configured-audience")

    settings = get_settings()

    assert settings.access_token_secret is not None
    assert settings.access_token_secret.get_secret_value() == secret
    assert settings.access_token_ttl_minutes == 30
    assert settings.access_token_issuer == "configured-issuer"
    assert settings.access_token_audience == "configured-audience"
    assert secret not in repr(settings)
    assert settings.model_dump(mode="json")["access_token_secret"] == "**********"


@pytest.mark.parametrize("ttl", [0, 61])
def test_access_token_ttl_rejects_values_outside_the_short_lived_bound(
    ttl: int,
) -> None:
    """Reject lifetimes outside the explicit 1-60 minute contract."""

    with pytest.raises(ValidationError, match="between 1 and 60 minutes"):
        Settings.model_validate({"access_token_ttl_minutes": ttl})


def test_access_token_secret_rejects_short_value_without_echoing_it() -> None:
    """Require at least 32 characters and keep rejected material secret."""

    secret = "short-synthetic-secret"

    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate({"access_token_secret": secret})

    assert "at least 32 characters" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_valid_synchronous_postgresql_url_is_accepted() -> None:
    """A complete postgresql+psycopg URL is valid configuration."""

    settings = Settings.model_validate({"database_url": VALID_DATABASE_URL})

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == VALID_DATABASE_URL


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://test_user:test_password@127.0.0.1/test_database",
        "postgresql+asyncpg://test_user:test_password@127.0.0.1/test_database",
        "sqlite:///test_database.db",
        "mysql://test_user:test_password@127.0.0.1/test_database",
        "not-a-database-url",
    ],
)
def test_non_psycopg_database_urls_are_rejected(database_url: str) -> None:
    """Only the planned synchronous PostgreSQL dialect and driver are accepted."""

    with pytest.raises(
        ValidationError,
        match=r"database URL must use the postgresql\+psycopg driver",
    ):
        Settings.model_validate({"database_url": database_url})


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://",
        "postgresql+psycopg:///test_database",
        "postgresql+psycopg://127.0.0.1",
        "postgresql+psycopg://127.0.0.1:not-a-port/test_database",
    ],
)
def test_incomplete_database_urls_are_rejected(database_url: str) -> None:
    """The configured URL must identify both a valid host and database."""

    with pytest.raises(ValidationError):
        Settings.model_validate({"database_url": database_url})


def test_application_and_liveness_work_without_database_url() -> None:
    """An unconfigured database must not break app creation or liveness."""

    application = create_app()

    with TestClient(application) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_url_is_hidden_from_repr_dump_and_validation_errors() -> None:
    """Common settings representations must not expose the database password."""

    settings = Settings.model_validate({"database_url": VALID_DATABASE_URL})
    python_dump = settings.model_dump()
    json_dump = settings.model_dump(mode="json")

    assert DATABASE_PASSWORD not in repr(settings)
    assert DATABASE_PASSWORD not in repr(python_dump)
    assert json_dump["database_url"] == "**********"

    invalid_url = (
        f"postgresql+asyncpg://test_user:{DATABASE_PASSWORD}@127.0.0.1/test_database"
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate({"database_url": invalid_url})

    assert DATABASE_PASSWORD not in str(exc_info.value)
