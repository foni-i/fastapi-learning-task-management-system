"""Connection-free metadata tests for the Project model."""

from typing import cast

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DefaultClause

from app.db.base import Base
from app.models import Project, ProjectStatus

EXPECTED_COLUMNS = {
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


def test_project_registers_in_shared_metadata_with_exact_columns() -> None:
    """Discover users and projects without registering future tables."""

    assert Project.metadata is Base.metadata
    assert set(Base.metadata.tables) == {"users", "projects", "tasks"}
    assert set(Project.__table__.columns.keys()) == EXPECTED_COLUMNS


def test_project_columns_have_exact_types_bounds_nullability_and_defaults() -> None:
    """Keep the ORM field contract aligned with PostgreSQL DDL."""

    table = cast(Table, Project.__table__)
    assert isinstance(table.c.id.type, postgresql.UUID)
    assert table.c.id.primary_key is True
    assert (
        str(cast(DefaultClause, table.c.id.server_default).arg) == "gen_random_uuid()"
    )
    assert isinstance(table.c.user_id.type, postgresql.UUID)
    assert table.c.user_id.nullable is False

    assert isinstance(table.c.name.type, String)
    assert table.c.name.type.length == 200
    assert table.c.name.nullable is False
    assert isinstance(table.c.description.type, String)
    assert table.c.description.type.length == 2000
    assert table.c.description.nullable is True

    for column_name in ("start_date", "target_date"):
        assert isinstance(table.c[column_name].type, Date)
        assert table.c[column_name].nullable is True

    assert isinstance(table.c.status.type, String)
    assert table.c.status.type.length == 20
    assert table.c.status.nullable is False
    assert (
        str(cast(DefaultClause, table.c.status.server_default).arg) == "'NOT_STARTED'"
    )

    for column_name in ("created_at", "updated_at"):
        column = table.c[column_name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert (
            str(cast(DefaultClause, column.server_default).arg) == "CURRENT_TIMESTAMP"
        )


def test_project_has_exact_named_constraints_and_owner_index() -> None:
    """Expose every Project integrity and ownership object by stable name."""

    table = cast(Table, Project.__table__)
    constraints = {constraint.name: constraint for constraint in table.constraints}
    assert set(constraints) == {
        "pk_projects",
        "fk_projects_user_id_users",
        "ck_projects_name_not_blank",
        "ck_projects_status",
        "ck_projects_target_date_not_before_start_date",
        "uq_projects_id_user_id",
    }
    foreign_key = constraints["fk_projects_user_id_users"]
    assert isinstance(foreign_key, ForeignKeyConstraint)
    assert tuple(element.target_fullname for element in foreign_key.elements) == (
        "users.id",
    )
    assert foreign_key.ondelete is None

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks["ck_projects_name_not_blank"] == "btrim(name) <> ''"
    assert "ARCHIVED" in checks["ck_projects_status"]
    assert (
        "target_date >= start_date"
        in checks["ck_projects_target_date_not_before_start_date"]
    )
    assert {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    } == {"ix_projects_user_id": ("user_id",)}


def test_project_status_has_only_the_fixed_values() -> None:
    """Keep model-facing status names equal to stored strings."""

    assert {status.value for status in ProjectStatus} == {
        "NOT_STARTED",
        "IN_PROGRESS",
        "COMPLETED",
        "ARCHIVED",
    }
