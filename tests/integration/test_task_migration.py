"""Real PostgreSQL round-trip verification for the Task migration."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PRE_TASK_REVISION = "4d8c7a1b2e90"
TASK_REVISION = "6e2f9a4c1b73"


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def assert_task_contract(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("tasks")}
    assert set(columns) == {
        "id",
        "user_id",
        "project_id",
        "title",
        "description",
        "status",
        "priority",
        "planned_date",
        "due_at",
        "estimated_minutes",
        "completed_at",
        "created_at",
        "updated_at",
    }
    assert inspector.get_pk_constraint("tasks")["name"] == "pk_tasks"
    assert {item["name"] for item in inspector.get_foreign_keys("tasks")} == {
        "fk_tasks_user_id_users",
        "fk_tasks_project_id_user_id_projects",
    }
    assert {item["name"] for item in inspector.get_check_constraints("tasks")} == {
        "ck_tasks_title_not_blank",
        "ck_tasks_status",
        "ck_tasks_priority",
        "ck_tasks_estimated_minutes",
        "ck_tasks_due_at_not_before_planned_date",
        "ck_tasks_completed_at_matches_status",
    }
    assert {item["name"] for item in inspector.get_indexes("tasks")} == {
        "ix_tasks_user_id",
        "ix_tasks_project_id",
        "ix_tasks_user_status",
        "ix_tasks_user_priority",
        "ix_tasks_user_due_at",
        "ix_tasks_user_created_at_id",
    }
    assert "uq_projects_id_user_id" in {
        item["name"] for item in inspector.get_unique_constraints("projects")
    }


def test_task_migration_upgrade_downgrade_reupgrade(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = migration_test_database_url.render_as_string(hide_password=False)
    config = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("STMS_DATABASE_URL", target)
    get_settings.cache_clear()
    try:
        validate_migration_test_target(target)
        command.downgrade(config, PRE_TASK_REVISION)
        assert current_revision(integration_engine) == PRE_TASK_REVISION
        assert "tasks" not in inspect(integration_engine).get_table_names()
        assert {"users", "projects"} <= set(
            inspect(integration_engine).get_table_names()
        )
        command.upgrade(config, TASK_REVISION)
        assert current_revision(integration_engine) == TASK_REVISION
        assert_task_contract(integration_engine)
        command.downgrade(config, PRE_TASK_REVISION)
        assert "tasks" not in inspect(integration_engine).get_table_names()
        command.upgrade(config, TASK_REVISION)
        assert_task_contract(integration_engine)
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()


def test_task_project_ownership_is_enforced_by_postgresql(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        first_user = User(
            email=f"task-owner-{uuid4()}@example.com",
            password_hash="synthetic-test-hash",
        )
        second_user = User(
            email=f"task-other-{uuid4()}@example.com",
            password_hash="synthetic-test-hash",
        )
        session.add_all([first_user, second_user])
        session.flush()
        project = Project(user_id=first_user.id, name="Ownership boundary")
        session.add(project)
        session.flush()

        session.add(
            Task(
                user_id=second_user.id,
                project_id=project.id,
                title="Cross-owner task",
            )
        )
        with pytest.raises(IntegrityError) as error:
            session.flush()

        diagnostic = getattr(error.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == (
            "fk_tasks_project_id_user_id_projects"
        )
        session.rollback()
