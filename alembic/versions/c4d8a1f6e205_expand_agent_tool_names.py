"""Expand the idempotency Tool-name allowlist.

Revision ID: c4d8a1f6e205
Revises: 8b7d4e2f1a90
Create Date: 2026-09-05 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d8a1f6e205"
down_revision: str | Sequence[str] | None = "8b7d4e2f1a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_agent_tool_executions_tool_name"


def upgrade() -> None:
    """Allow the two approved high-impact Tool capabilities."""

    op.drop_constraint(
        CONSTRAINT_NAME,
        "agent_tool_executions",
        type_="check",
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "agent_tool_executions",
        "tool_name IN ('create_task', 'update_task', "
        "'batch_create_tasks', 'delete_task')",
    )


def downgrade() -> None:
    """Restore the original Task 10.5 Tool-name allowlist."""

    op.drop_constraint(
        CONSTRAINT_NAME,
        "agent_tool_executions",
        type_="check",
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "agent_tool_executions",
        "tool_name IN ('create_task', 'update_task')",
    )
