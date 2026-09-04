"""Metadata tests for the fixed Stage 7 Task storage contract."""

from typing import cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Table,
    UniqueConstraint,
)
from sqlalchemy.schema import DefaultClause

from app.db.base import Base
from app.models import Project, Task, TaskPriority, TaskStatus


def test_task_enums_and_exact_columns() -> None:
    assert [item.value for item in TaskStatus] == [
        "TODO",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
    ]
    assert [item.value for item in TaskPriority] == ["LOW", "MEDIUM", "HIGH", "URGENT"]
    assert set(Task.__table__.columns.keys()) == {
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
    assert set(Base.metadata.tables) == {
        "users",
        "projects",
        "tasks",
        "agent_threads",
        "agent_runs",
        "agent_approvals",
    }


def test_task_types_nullability_and_defaults() -> None:
    columns = cast(Table, Task.__table__).columns
    assert str(columns["id"].type) == "UUID"
    assert "gen_random_uuid" in str(
        cast(DefaultClause, columns["id"].server_default).arg
    )
    assert str(columns["user_id"].type) == "UUID"
    assert str(columns["project_id"].type) == "UUID"
    assert str(columns["title"].type) == "VARCHAR(300)"
    assert str(columns["description"].type) == "VARCHAR(5000)"
    assert str(columns["status"].type) == "VARCHAR(20)"
    assert "TODO" in str(cast(DefaultClause, columns["status"].server_default).arg)
    assert str(columns["priority"].type) == "VARCHAR(10)"
    assert "MEDIUM" in str(cast(DefaultClause, columns["priority"].server_default).arg)
    assert columns["title"].nullable is False
    for name in (
        "description",
        "planned_date",
        "due_at",
        "estimated_minutes",
        "completed_at",
    ):
        assert columns[name].nullable is True
    for name in ("due_at", "completed_at", "created_at", "updated_at"):
        column_type = columns[name].type
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


def test_task_named_constraints_indexes_and_project_owner_key() -> None:
    task_table = cast(Table, Task.__table__)
    constraints = {item.name: item for item in task_table.constraints}
    assert {
        "pk_tasks",
        "fk_tasks_user_id_users",
        "fk_tasks_project_id_user_id_projects",
        "ck_tasks_title_not_blank",
        "ck_tasks_status",
        "ck_tasks_priority",
        "ck_tasks_estimated_minutes",
        "ck_tasks_due_at_not_before_planned_date",
        "ck_tasks_completed_at_matches_status",
    } <= set(constraints)
    assert isinstance(constraints["fk_tasks_user_id_users"], ForeignKeyConstraint)
    assert isinstance(constraints["ck_tasks_status"], CheckConstraint)
    indexes = {item.name for item in task_table.indexes if isinstance(item, Index)}
    assert indexes == {
        "ix_tasks_user_id",
        "ix_tasks_project_id",
        "ix_tasks_user_status",
        "ix_tasks_user_priority",
        "ix_tasks_user_due_at",
        "ix_tasks_user_created_at_id",
    }
    project_table = cast(Table, Project.__table__)
    project_uniques = {
        item.name
        for item in project_table.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert "uq_projects_id_user_id" in project_uniques
