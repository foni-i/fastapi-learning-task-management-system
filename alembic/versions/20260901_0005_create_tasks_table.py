"""Create the owner-consistent tasks table.

Revision ID: 6e2f9a4c1b73
Revises: 4d8c7a1b2e90
Create Date: 2026-09-01 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6e2f9a4c1b73"
down_revision: str | Sequence[str] | None = "4d8c7a1b2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete Stage 7 Task storage contract."""

    op.create_unique_constraint("uq_projects_id_user_id", "projects", ["id", "user_id"])
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.String(length=5000), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'TODO'"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(length=10),
            server_default=sa.text("'MEDIUM'"),
            nullable=False,
        ),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_tasks_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["projects.id", "projects.user_id"],
            name="fk_tasks_project_id_user_id_projects",
        ),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_tasks_title_not_blank"),
        sa.CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')",
            name="ck_tasks_priority",
        ),
        sa.CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 1440",
            name="ck_tasks_estimated_minutes",
        ),
        sa.CheckConstraint(
            "due_at IS NULL OR planned_date IS NULL OR "
            "due_at >= (planned_date::timestamp AT TIME ZONE 'UTC')",
            name="ck_tasks_due_at_not_before_planned_date",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="ck_tasks_completed_at_matches_status",
        ),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"], unique=False)
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"], unique=False)
    op.create_index(
        "ix_tasks_user_status", "tasks", ["user_id", "status"], unique=False
    )
    op.create_index(
        "ix_tasks_user_priority", "tasks", ["user_id", "priority"], unique=False
    )
    op.create_index(
        "ix_tasks_user_due_at", "tasks", ["user_id", "due_at"], unique=False
    )
    op.create_index(
        "ix_tasks_user_created_at_id",
        "tasks",
        ["user_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only Stage 7 Task storage objects."""

    op.drop_table("tasks")
    op.drop_constraint("uq_projects_id_user_id", "projects", type_="unique")
