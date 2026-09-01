"""Real PostgreSQL round-trip verification for the Project migration."""

from pathlib import Path
from typing import cast

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Date, DateTime, inspect
from sqlalchemy.engine import URL, Engine

from alembic import command
from app.core.config import get_settings
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
PRE_PROJECT_REVISION = "9f3b2d6e8a41"
PROJECT_REVISION = "4d8c7a1b2e90"


def get_current_revision(engine: Engine) -> str | None:
    """Read the currently applied revision."""

    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def assert_project_table_contract(engine: Engine) -> None:
    """Inspect every Project column and named integrity object."""

    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("projects", schema="public")
    }
    assert set(columns) == {
        "id",
        "user_id",
        "name",
        "description",
        "start_date",
        "target_date",
        "status",
        "created_at",
        "updated_at",
    }
    assert str(columns["id"]["type"]) == "UUID"
    assert "gen_random_uuid" in str(columns["id"]["default"])
    assert str(columns["user_id"]["type"]) == "UUID"
    assert str(columns["name"]["type"]) == "VARCHAR(200)"
    assert str(columns["description"]["type"]) == "VARCHAR(2000)"
    assert columns["description"]["nullable"] is True
    assert isinstance(columns["start_date"]["type"], Date)
    assert isinstance(columns["target_date"]["type"], Date)
    assert str(columns["status"]["type"]) == "VARCHAR(20)"
    assert "NOT_STARTED" in str(columns["status"]["default"])
    for name in ("created_at", "updated_at"):
        timestamp_type = cast(DateTime, columns[name]["type"])
        assert isinstance(timestamp_type, DateTime)
        assert timestamp_type.timezone is True

    assert (
        inspector.get_pk_constraint("projects", schema="public")["name"]
        == "pk_projects"
    )
    assert {
        item["name"] for item in inspector.get_foreign_keys("projects", schema="public")
    } == {"fk_projects_user_id_users"}
    assert {
        item["name"]
        for item in inspector.get_check_constraints("projects", schema="public")
    } == {
        "ck_projects_name_not_blank",
        "ck_projects_status",
        "ck_projects_target_date_not_before_start_date",
    }
    assert {
        item["name"] for item in inspector.get_indexes("projects", schema="public")
    } == {"ix_projects_user_id"}


def test_project_migration_upgrade_downgrade_and_reupgrade(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip only on the guarded disposable PostgreSQL database."""

    target_url = migration_test_database_url.render_as_string(hide_password=False)
    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    monkeypatch.setenv("STMS_DATABASE_URL", target_url)
    get_settings.cache_clear()

    try:
        validate_migration_test_target(target_url)
        command.downgrade(configuration, PRE_PROJECT_REVISION)
        assert get_current_revision(integration_engine) == PRE_PROJECT_REVISION
        tables = set(inspect(integration_engine).get_table_names(schema="public"))
        assert "users" in tables
        assert "projects" not in tables

        command.upgrade(configuration, PROJECT_REVISION)
        assert get_current_revision(integration_engine) == PROJECT_REVISION
        assert_project_table_contract(integration_engine)

        command.downgrade(configuration, PRE_PROJECT_REVISION)
        assert get_current_revision(integration_engine) == PRE_PROJECT_REVISION
        tables = set(inspect(integration_engine).get_table_names(schema="public"))
        assert "users" in tables
        assert "projects" not in tables

        command.upgrade(configuration, PROJECT_REVISION)
        assert get_current_revision(integration_engine) == PROJECT_REVISION
        assert_project_table_contract(integration_engine)
    finally:
        command.upgrade(configuration, "head")
        get_settings.cache_clear()
