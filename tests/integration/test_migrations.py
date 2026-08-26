"""Alembic baseline chain and PostgreSQL round-trip verification."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Engine

from alembic import command
from app.core.config import get_settings
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "2f6a8c1d4b90"
ALLOWED_BASELINE_TABLES = {"alembic_version"}


def get_current_revision(engine: Engine) -> str | None:
    """Read the current revision through a short-lived connection."""

    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def get_public_tables(engine: Engine) -> set[str]:
    """Return non-system tables from PostgreSQL's public schema."""

    with engine.connect() as connection:
        return set(inspect(connection).get_table_names(schema="public"))


def assert_only_alembic_tables(engine: Engine) -> None:
    """Reject any product or unexplained table throughout the baseline cycle."""

    assert get_public_tables(engine) <= ALLOWED_BASELINE_TABLES


def test_stage_2_baseline_remains_a_reachable_empty_revision(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the empty Stage 2 boundary remains reachable in the longer chain."""

    target_url = migration_test_database_url.render_as_string(hide_password=False)
    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    monkeypatch.setenv("STMS_DATABASE_URL", target_url)
    get_settings.cache_clear()

    try:
        validate_migration_test_target(target_url)
        command.downgrade(configuration, "base")
        assert get_current_revision(integration_engine) is None
        assert_only_alembic_tables(integration_engine)

        command.upgrade(configuration, BASELINE_REVISION)
        assert get_current_revision(integration_engine) == BASELINE_REVISION
        assert get_public_tables(integration_engine) == ALLOWED_BASELINE_TABLES

        validate_migration_test_target(target_url)
        command.downgrade(configuration, "base")
        assert get_current_revision(integration_engine) is None
        assert_only_alembic_tables(integration_engine)

        command.upgrade(configuration, BASELINE_REVISION)
        assert get_current_revision(integration_engine) == BASELINE_REVISION
        assert get_public_tables(integration_engine) == ALLOWED_BASELINE_TABLES
    finally:
        command.upgrade(configuration, "head")
        get_settings.cache_clear()
