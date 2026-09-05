"""Create safe Agent Tool execution audit records.

Revision ID: 8b7d4e2f1a90
Revises: 21ec26a7c672
Create Date: 2026-09-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8b7d4e2f1a90"
down_revision: str | Sequence[str] | None = "21ec26a7c672"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the product-owned idempotency audit table."""

    op.create_table(
        "agent_tool_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("proposal_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'IN_PROGRESS'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("result_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_summary", sa.String(length=300), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_executions"),
        sa.ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_agent_tool_executions_run_id_user_id_agent_runs",
        ),
        sa.UniqueConstraint(
            "run_id",
            "revision",
            "proposal_fingerprint",
            "action_key",
            name="uq_agent_tool_executions_action_identity",
        ),
        sa.CheckConstraint(
            "revision BETWEEN 0 AND 2", name="ck_agent_tool_executions_revision"
        ),
        sa.CheckConstraint(
            "proposal_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_executions_proposal_fingerprint",
        ),
        sa.CheckConstraint(
            "action_key ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_agent_tool_executions_action_key",
        ),
        sa.CheckConstraint(
            "tool_name IN ('create_task', 'update_task')",
            name="ck_agent_tool_executions_tool_name",
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'UNKNOWN')",
            name="ck_agent_tool_executions_status",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 1 AND 100",
            name="ck_agent_tool_executions_attempt_count",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_agent_tool_executions_error_code",
        ),
        sa.CheckConstraint(
            "(status = 'IN_PROGRESS' AND result_task_id IS NULL AND "
            "result_summary IS NULL AND error_code IS NULL AND completed_at IS NULL) "
            "OR (status = 'COMPLETED' AND result_task_id IS NOT NULL AND "
            "result_summary IS NOT NULL AND error_code IS NULL AND "
            "completed_at IS NOT NULL) OR (status IN ('FAILED', 'UNKNOWN') AND "
            "result_task_id IS NULL AND result_summary IS NULL AND "
            "error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_agent_tool_executions_state",
        ),
    )
    op.create_index(
        "ix_agent_tool_executions_run_id", "agent_tool_executions", ["run_id"]
    )
    op.create_index(
        "ix_agent_tool_executions_user_id", "agent_tool_executions", ["user_id"]
    )
    op.create_index(
        "ix_agent_tool_executions_user_status",
        "agent_tool_executions",
        ["user_id", "status"],
    )


def downgrade() -> None:
    """Drop only the Task 10.5 application table."""

    op.drop_index(
        "ix_agent_tool_executions_user_status",
        table_name="agent_tool_executions",
    )
    op.drop_index(
        "ix_agent_tool_executions_user_id", table_name="agent_tool_executions"
    )
    op.drop_index("ix_agent_tool_executions_run_id", table_name="agent_tool_executions")
    op.drop_table("agent_tool_executions")
