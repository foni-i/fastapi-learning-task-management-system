"""Create the user-owned projects table.

Revision ID: 4d8c7a1b2e90
Revises: 9f3b2d6e8a41
Create Date: 2026-08-31 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d8c7a1b2e90"
down_revision: str | Sequence[str] | None = "9f3b2d6e8a41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete Stage 6 Project storage contract."""

    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'NOT_STARTED'"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_projects_user_id_users",
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''",
            name="ck_projects_name_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'ARCHIVED')",
            name="ck_projects_status",
        ),
        sa.CheckConstraint(
            "target_date IS NULL OR start_date IS NULL OR target_date >= start_date",
            name="ck_projects_target_date_not_before_start_date",
        ),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"], unique=False)


def downgrade() -> None:
    """Remove only the projects table and its owned database objects."""

    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
