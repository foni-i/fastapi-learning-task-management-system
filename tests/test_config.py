"""Tests for typed application settings."""

from pathlib import Path

import pytest

from app.core.config import AppEnvironment, Settings


def test_settings_use_safe_defaults_without_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing optional .env file must not prevent settings from loading."""

    monkeypatch.chdir(tmp_path)
    for variable_name in (
        "STMS_APP_NAME",
        "STMS_ENV",
        "STMS_DEBUG",
        "STMS_API_DOCS_ENABLED",
    ):
        monkeypatch.delenv(variable_name, raising=False)

    settings = Settings()

    assert settings.app_name == "FastAPI STMS"
    assert settings.env is AppEnvironment.DEVELOPMENT
    assert settings.debug is False
    assert settings.api_docs_enabled is True


def test_settings_load_prefixed_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STMS-prefixed environment variables override the defaults with typed values."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STMS_APP_NAME", "Configured STMS")
    monkeypatch.setenv("STMS_ENV", "test")
    monkeypatch.setenv("STMS_DEBUG", "true")
    monkeypatch.setenv("STMS_API_DOCS_ENABLED", "false")

    settings = Settings()

    assert settings.app_name == "Configured STMS"
    assert settings.env is AppEnvironment.TEST
    assert settings.debug is True
    assert settings.api_docs_enabled is False
