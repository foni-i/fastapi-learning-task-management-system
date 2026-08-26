"""Real PostgreSQL verification for the foundational users migration."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, inspect
from sqlalchemy.engine import URL, Engine

from alembic import command
from app.core.config import get_settings
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "2f6a8c1d4b90"
USER_REVISION = "7c9e1b4a6d32"
EXPECTED_USER_COLUMNS = {
    "id",
    "email",
    "password_hash",
    "created_at",
    "updated_at",
}


def get_current_revision(engine: Engine) -> str | None:
    """Read the current revision using a short-lived connection."""

    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def assert_users_table_contract(engine: Engine) -> None:
    """Inspect exact columns and prove Task 3.2 uniqueness is still absent."""

    inspector = inspect(engine)
    assert set(inspector.get_table_names(schema="public")) == {
        "alembic_version",
        "users",
    }

    columns = {
        column["name"]: column
        for column in inspector.get_columns("users", schema="public")
    }
    assert set(columns) == EXPECTED_USER_COLUMNS
    assert str(columns["id"]["type"]) == "UUID"
    assert columns["id"]["nullable"] is False
    identifier_default = columns["id"]["default"]
    assert identifier_default is not None
    assert "gen_random_uuid" in identifier_default
    assert str(columns["email"]["type"]) == "VARCHAR(254)"
    assert columns["email"]["nullable"] is False
    assert str(columns["password_hash"]["type"]) == "VARCHAR(255)"
    assert columns["password_hash"]["nullable"] is False

    for column_name in ("created_at", "updated_at"):
        timestamp = columns[column_name]
        assert isinstance(timestamp["type"], DateTime)
        assert timestamp["type"].timezone is True
        assert timestamp["nullable"] is False
        assert timestamp["default"] == "CURRENT_TIMESTAMP"

    primary_key = inspector.get_pk_constraint("users", schema="public")
    assert primary_key["constrained_columns"] == ["id"]
    assert inspector.get_unique_constraints("users", schema="public") == []
    assert inspector.get_indexes("users", schema="public") == []


def test_user_migration_upgrade_downgrade_and_reupgrade(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip users only on the guarded disposable PostgreSQL database."""

    target_url = migration_test_database_url.render_as_string(hide_password=False)
    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    monkeypatch.setenv("STMS_DATABASE_URL", target_url)
    get_settings.cache_clear()

    try:
        validate_migration_test_target(target_url)
        command.downgrade(configuration, BASELINE_REVISION)
        assert get_current_revision(integration_engine) == BASELINE_REVISION
        assert "users" not in inspect(integration_engine).get_table_names(
            schema="public"
        )

        command.upgrade(configuration, "head")
        assert get_current_revision(integration_engine) == USER_REVISION
        assert_users_table_contract(integration_engine)

        validate_migration_test_target(target_url)
        command.downgrade(configuration, BASELINE_REVISION)
        assert get_current_revision(integration_engine) == BASELINE_REVISION
        assert "users" not in inspect(integration_engine).get_table_names(
            schema="public"
        )

        command.upgrade(configuration, "head")
        assert get_current_revision(integration_engine) == USER_REVISION
        assert_users_table_contract(integration_engine)
        command.check(configuration)
    finally:
        command.upgrade(configuration, "head")
        get_settings.cache_clear()
