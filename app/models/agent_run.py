"""Product-owned Agent thread, run, and approval audit records."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentThreadStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


class AgentApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class AgentThread(Base):
    """Persist one owner-scoped product thread, not graph checkpoint state."""

    __tablename__ = "agent_threads"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_threads"),
        UniqueConstraint("id", "user_id", name="uq_agent_threads_id_user_id"),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_threads_user_id_users"
        ),
        CheckConstraint(
            "btrim(goal_summary) <> ''", name="ck_agent_threads_goal_summary_not_blank"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'FAILED')",
            name="ck_agent_threads_status",
        ),
        Index("ix_agent_threads_user_id", "user_id"),
        Index("ix_agent_threads_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    goal_summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AgentRun(Base):
    """Persist safe product execution status and aggregate metrics."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_runs"),
        UniqueConstraint("id", "user_id", name="uq_agent_runs_id_user_id"),
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["agent_threads.id", "agent_threads.user_id"],
            name="fk_agent_runs_thread_id_user_id_agent_threads",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PENDING_APPROVAL', 'SUCCEEDED', "
            "'REJECTED', 'PARTIAL_FAILURE', 'FAILED')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "current_node IS NULL OR current_node IN "
            "('analyze_goal', 'load_context', 'generate_plan', 'validate_plan', "
            "'request_approval', 'execute_tasks', 'verify_result', 'summarize')",
            name="ck_agent_runs_current_node",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_agent_runs_error_code",
        ),
        CheckConstraint(
            "model_round_count >= 0 AND provider_attempt_count >= 0 AND "
            "tool_call_count >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND total_tokens >= 0 AND latency_ms >= 0",
            name="ck_agent_runs_metrics_nonnegative",
        ),
        CheckConstraint(
            "total_tokens = input_tokens + output_tokens",
            name="ck_agent_runs_total_tokens",
        ),
        Index("ix_agent_runs_thread_id", "thread_id"),
        Index("ix_agent_runs_user_id", "user_id"),
        Index("ix_agent_runs_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    thread_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PENDING'")
    )
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_round_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    provider_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tool_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    latency_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AgentApproval(Base):
    """Persist one owner decision for one exact proposal revision."""

    __tablename__ = "agent_approvals"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_approvals"),
        ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_agent_approvals_run_id_user_id_agent_runs",
        ),
        UniqueConstraint(
            "run_id", "revision", name="uq_agent_approvals_run_id_revision"
        ),
        CheckConstraint("revision BETWEEN 0 AND 2", name="ck_agent_approvals_revision"),
        CheckConstraint(
            "proposal_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_approvals_proposal_fingerprint",
        ),
        CheckConstraint(
            "decision IN ('PENDING', 'APPROVED', 'REJECTED', 'REQUEST_CHANGES')",
            name="ck_agent_approvals_decision",
        ),
        CheckConstraint(
            "(decision = 'PENDING' AND decided_at IS NULL AND feedback IS NULL) OR "
            "(decision IN ('APPROVED', 'REJECTED') AND decided_at IS NOT NULL "
            "AND feedback IS NULL) OR "
            "(decision = 'REQUEST_CHANGES' AND decided_at IS NOT NULL "
            "AND feedback IS NOT NULL AND btrim(feedback) <> '')",
            name="ck_agent_approvals_decision_state",
        ),
        Index("ix_agent_approvals_run_id", "run_id"),
        Index("ix_agent_approvals_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PENDING'")
    )
    feedback: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
