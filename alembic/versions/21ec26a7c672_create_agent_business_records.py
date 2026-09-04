"""Create product-owned Agent thread, run, and approval records.

Revision ID: 21ec26a7c672
Revises: 6e2f9a4c1b73
Create Date: 2026-09-04 15:17:08.807841

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "21ec26a7c672"
down_revision: str | Sequence[str] | None = "6e2f9a4c1b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create product audit records without creating Checkpoint tables."""

    op.create_table(
        "agent_threads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_summary", sa.String(length=2000), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'ACTIVE'"),
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_threads"),
        sa.UniqueConstraint("id", "user_id", name="uq_agent_threads_id_user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_threads_user_id_users"
        ),
        sa.CheckConstraint(
            "btrim(goal_summary) <> ''",
            name="ck_agent_threads_goal_summary_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'FAILED')",
            name="ck_agent_threads_status",
        ),
    )
    op.create_index("ix_agent_threads_user_id", "agent_threads", ["user_id"])
    op.create_index(
        "ix_agent_threads_user_status", "agent_threads", ["user_id", "status"]
    )

    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("current_node", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.String(length=2000), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "model_round_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "provider_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tool_call_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "latency_ms", sa.Float(), server_default=sa.text("0"), nullable=False
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint("id", "user_id", name="uq_agent_runs_id_user_id"),
        sa.ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["agent_threads.id", "agent_threads.user_id"],
            name="fk_agent_runs_thread_id_user_id_agent_threads",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PENDING_APPROVAL', 'SUCCEEDED', "
            "'REJECTED', 'PARTIAL_FAILURE', 'FAILED')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "current_node IS NULL OR current_node IN "
            "('analyze_goal', 'load_context', 'generate_plan', 'validate_plan', "
            "'request_approval', 'execute_tasks', 'verify_result', 'summarize')",
            name="ck_agent_runs_current_node",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_agent_runs_error_code",
        ),
        sa.CheckConstraint(
            "model_round_count >= 0 AND provider_attempt_count >= 0 AND "
            "tool_call_count >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND total_tokens >= 0 AND latency_ms >= 0",
            name="ck_agent_runs_metrics_nonnegative",
        ),
        sa.CheckConstraint(
            "total_tokens = input_tokens + output_tokens",
            name="ck_agent_runs_total_tokens",
        ),
    )
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_user_status", "agent_runs", ["user_id", "status"])

    op.create_table(
        "agent_approvals",
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
        sa.Column(
            "decision",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("feedback", sa.String(length=1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_approvals"),
        sa.ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_agent_approvals_run_id_user_id_agent_runs",
        ),
        sa.UniqueConstraint(
            "run_id", "revision", name="uq_agent_approvals_run_id_revision"
        ),
        sa.CheckConstraint(
            "revision BETWEEN 0 AND 2", name="ck_agent_approvals_revision"
        ),
        sa.CheckConstraint(
            "proposal_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_approvals_proposal_fingerprint",
        ),
        sa.CheckConstraint(
            "decision IN ('PENDING', 'APPROVED', 'REJECTED', 'REQUEST_CHANGES')",
            name="ck_agent_approvals_decision",
        ),
        sa.CheckConstraint(
            "(decision = 'PENDING' AND decided_at IS NULL AND feedback IS NULL) OR "
            "(decision IN ('APPROVED', 'REJECTED') AND decided_at IS NOT NULL "
            "AND feedback IS NULL) OR "
            "(decision = 'REQUEST_CHANGES' AND decided_at IS NOT NULL "
            "AND feedback IS NOT NULL AND btrim(feedback) <> '')",
            name="ck_agent_approvals_decision_state",
        ),
    )
    op.create_index("ix_agent_approvals_run_id", "agent_approvals", ["run_id"])
    op.create_index("ix_agent_approvals_user_id", "agent_approvals", ["user_id"])


def downgrade() -> None:
    """Drop only product-owned Agent business records."""

    op.drop_index("ix_agent_approvals_user_id", table_name="agent_approvals")
    op.drop_index("ix_agent_approvals_run_id", table_name="agent_approvals")
    op.drop_table("agent_approvals")
    op.drop_index("ix_agent_runs_user_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_thread_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_threads_user_status", table_name="agent_threads")
    op.drop_index("ix_agent_threads_user_id", table_name="agent_threads")
    op.drop_table("agent_threads")
