"""Tests for the connection-free Alembic migration environment."""

from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from runpy import run_path
from unittest.mock import MagicMock, Mock

import psycopg
import pytest
import sqlalchemy
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import pool

from alembic import context as alembic_context
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import DatabaseConfigurationError
from app.main import create_app
from app.models import User

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIRECTORY = PROJECT_ROOT / "alembic"
ALEMBIC_ENV_PATH = ALEMBIC_DIRECTORY / "env.py"
ALEMBIC_VERSIONS_DIRECTORY = ALEMBIC_DIRECTORY / "versions"
BASELINE_REVISION = "2f6a8c1d4b90"
USER_REVISION = "7c9e1b4a6d32"
DATABASE_PASSWORD = "test-only-password"
VALID_DATABASE_URL = (
    f"postgresql+psycopg://test_user:{DATABASE_PASSWORD}@127.0.0.1:5432/test_database"
)


def fail_database_connection(*args: object, **kwargs: object) -> None:
    """Fail immediately if a focused configuration test reaches Psycopg."""

    raise AssertionError("Task 2.3 tests must not connect to PostgreSQL")


@pytest.fixture(autouse=True)
def isolate_alembic_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Isolate settings and forbid network connections in every focused test."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STMS_DATABASE_URL", raising=False)
    monkeypatch.setattr(psycopg, "connect", fail_database_connection)
    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


def configure_alembic_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    offline: bool,
    captured_configuration: dict[str, object],
) -> None:
    """Replace Alembic runtime proxies with connection-free test doubles."""

    monkeypatch.setattr(alembic_context, "config", Config(), raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: offline)
    monkeypatch.setattr(
        alembic_context,
        "configure",
        lambda **kwargs: captured_configuration.update(kwargs),
    )
    monkeypatch.setattr(alembic_context, "begin_transaction", nullcontext)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: None)


def test_alembic_configuration_loads_linear_user_migration_chain() -> None:
    """The config resolves the Stage 2 baseline followed by the users revision."""

    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    script_directory = ScriptDirectory.from_config(configuration)
    script_location = configuration.get_main_option("script_location")

    assert script_location is not None
    assert Path(script_location).resolve() == ALEMBIC_DIRECTORY.resolve()
    assert configuration.get_main_option("sqlalchemy.url") is None
    assert Path(script_directory.dir).resolve() == ALEMBIC_DIRECTORY.resolve()
    assert ALEMBIC_VERSIONS_DIRECTORY.is_dir()
    revisions = list(script_directory.walk_revisions())
    assert len(list(ALEMBIC_VERSIONS_DIRECTORY.glob("*.py"))) == 2
    assert script_directory.get_heads() == [USER_REVISION]
    assert len(revisions) == 2
    assert revisions[0].revision == USER_REVISION
    assert revisions[0].down_revision == BASELINE_REVISION
    assert revisions[1].revision == BASELINE_REVISION
    assert revisions[1].down_revision is None


def test_alembic_config_contains_no_database_url_or_password() -> None:
    """The committed INI owns migration behavior but never database secrets."""

    config_text = ALEMBIC_CONFIG_PATH.read_text(encoding="utf-8").lower()

    assert "postgresql+psycopg://" not in config_text
    assert "sqlalchemy.url" not in config_text
    assert "password" not in config_text
    assert DATABASE_PASSWORD not in config_text


def test_offline_environment_uses_settings_and_base_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline configuration reads typed settings and targets shared metadata."""

    monkeypatch.setenv("STMS_DATABASE_URL", VALID_DATABASE_URL)
    captured_configuration: dict[str, object] = {}
    configure_alembic_context(
        monkeypatch,
        offline=True,
        captured_configuration=captured_configuration,
    )

    run_path(str(ALEMBIC_ENV_PATH), run_name="alembic_env_offline_test")

    assert captured_configuration["url"] == VALID_DATABASE_URL
    assert captured_configuration["target_metadata"] is Base.metadata
    assert captured_configuration["literal_binds"] is True
    assert captured_configuration["compare_type"] is True
    assert captured_configuration["compare_server_default"] is True
    assert "render_as_batch" not in captured_configuration
    assert captured_configuration["target_metadata"] is User.metadata
    assert set(Base.metadata.tables) == {"users"}


def test_online_environment_uses_sync_engine_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The future online path uses synchronous create_engine and controlled I/O."""

    monkeypatch.setenv("STMS_DATABASE_URL", VALID_DATABASE_URL)
    captured_configuration: dict[str, object] = {}
    captured_engine_arguments: dict[str, object] = {}
    configure_alembic_context(
        monkeypatch,
        offline=False,
        captured_configuration=captured_configuration,
    )
    fake_connection = Mock()
    fake_engine = MagicMock()
    fake_engine.connect.return_value.__enter__.return_value = fake_connection

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_engine_arguments["url"] = url
        captured_engine_arguments.update(kwargs)
        return fake_engine

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    run_path(str(ALEMBIC_ENV_PATH), run_name="alembic_env_online_test")

    assert captured_engine_arguments["url"] == VALID_DATABASE_URL
    assert captured_engine_arguments["poolclass"] is pool.NullPool
    assert captured_configuration["connection"] is fake_connection
    assert captured_configuration["target_metadata"] is Base.metadata
    assert captured_configuration["compare_type"] is True
    assert captured_configuration["compare_server_default"] is True
    fake_engine.connect.assert_called_once_with()
    fake_engine.dispose.assert_called_once_with()


def test_missing_database_url_is_safe_for_app_and_explicit_for_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only migration execution needs the URL; app liveness remains independent."""

    application = create_app()
    with TestClient(application) as client:
        response = client.get("/health/live")

    captured_configuration: dict[str, object] = {}
    configure_alembic_context(
        monkeypatch,
        offline=True,
        captured_configuration=captured_configuration,
    )
    with pytest.raises(DatabaseConfigurationError) as exc_info:
        run_path(str(ALEMBIC_ENV_PATH), run_name="alembic_env_missing_url_test")

    error_message = str(exc_info.value)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert error_message == "STMS_DATABASE_URL is not configured"
    assert "postgresql" not in error_message
    assert "@" not in error_message
    assert DATABASE_PASSWORD not in error_message
