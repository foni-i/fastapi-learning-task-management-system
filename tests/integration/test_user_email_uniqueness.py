"""Real PostgreSQL tests for canonical user email uniqueness."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.core.email_normalization import normalize_email
from app.models import User
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "2f6a8c1d4b90"
USER_REVISION = "7c9e1b4a6d32"
EMAIL_UNIQUE_REVISION = "9f3b2d6e8a41"
EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


def get_current_revision(engine: Engine) -> str | None:
    """Read the current revision using a short-lived connection."""

    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def get_user_unique_constraints(engine: Engine) -> dict[str | None, list[str]]:
    """Return PostgreSQL's reflected named unique constraints for users."""

    return {
        constraint["name"]: constraint["column_names"]
        for constraint in inspect(engine).get_unique_constraints(
            "users",
            schema="public",
        )
    }


def test_email_unique_migration_upgrade_downgrade_and_reupgrade(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add and remove only the named constraint above the Task 3.1 revision."""

    target_url = migration_test_database_url.render_as_string(hide_password=False)
    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    monkeypatch.setenv("STMS_DATABASE_URL", target_url)
    get_settings.cache_clear()

    try:
        validate_migration_test_target(target_url)
        current_revision = get_current_revision(integration_engine)
        if current_revision in (None, BASELINE_REVISION):
            command.upgrade(configuration, USER_REVISION)
        elif current_revision != USER_REVISION:
            command.downgrade(configuration, USER_REVISION)
        assert get_current_revision(integration_engine) == USER_REVISION
        assert "users" in inspect(integration_engine).get_table_names(schema="public")
        assert EMAIL_UNIQUE_CONSTRAINT not in get_user_unique_constraints(
            integration_engine
        )

        command.upgrade(configuration, EMAIL_UNIQUE_REVISION)
        assert get_current_revision(integration_engine) == EMAIL_UNIQUE_REVISION
        assert get_user_unique_constraints(integration_engine) == {
            EMAIL_UNIQUE_CONSTRAINT: ["email"]
        }

        validate_migration_test_target(target_url)
        command.downgrade(configuration, USER_REVISION)
        assert get_current_revision(integration_engine) == USER_REVISION
        assert "users" in inspect(integration_engine).get_table_names(schema="public")
        assert EMAIL_UNIQUE_CONSTRAINT not in get_user_unique_constraints(
            integration_engine
        )

        command.upgrade(configuration, EMAIL_UNIQUE_REVISION)
        assert get_current_revision(integration_engine) == EMAIL_UNIQUE_REVISION
        assert get_user_unique_constraints(integration_engine) == {
            EMAIL_UNIQUE_CONSTRAINT: ["email"]
        }

        command.upgrade(configuration, "head")
        command.check(configuration)
    finally:
        command.upgrade(configuration, "head")
        get_settings.cache_clear()


def test_postgresql_rejects_duplicate_canonical_email_and_rolls_back(
    db_session: Session,
) -> None:
    """Identify the named final defense and leave the test transaction clean."""

    first_email = normalize_email("  User@EXAMPLE.COM ")
    second_email = normalize_email("user@example.com")
    assert first_email == second_email

    db_session.add(User(email=first_email, password_hash="test-only-hash"))
    db_session.flush()

    with pytest.raises(IntegrityError) as exc_info, db_session.begin_nested():
        db_session.add(User(email=second_email, password_hash="another-test-only-hash"))
        db_session.flush()

    diagnostic = getattr(exc_info.value.orig, "diag", None)
    assert getattr(diagnostic, "constraint_name", None) == EMAIL_UNIQUE_CONSTRAINT

    db_session.rollback()
    remaining_test_users = db_session.scalar(
        select(func.count()).select_from(User).where(User.email == first_email)
    )
    assert remaining_test_users == 0
