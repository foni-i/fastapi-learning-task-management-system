"""Safe product audit records for idempotent Agent Tool writes."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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


class AgentToolExecutionStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class AgentToolExecution(Base):
    """Persist bounded execution identity and outcome without Tool arguments."""

    __tablename__ = "agent_tool_executions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_tool_executions"),
        ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_agent_tool_executions_run_id_user_id_agent_runs",
        ),
        UniqueConstraint(
            "run_id",
            "revision",
            "proposal_fingerprint",
            "action_key",
            name="uq_agent_tool_executions_action_identity",
        ),
        CheckConstraint(
            "revision BETWEEN 0 AND 2", name="ck_agent_tool_executions_revision"
        ),
        CheckConstraint(
            "proposal_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_executions_proposal_fingerprint",
        ),
        CheckConstraint(
            "action_key ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_agent_tool_executions_action_key",
        ),
        CheckConstraint(
            "tool_name IN ('create_task', 'update_task', "
            "'batch_create_tasks', 'delete_task')",
            name="ck_agent_tool_executions_tool_name",
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'UNKNOWN')",
            name="ck_agent_tool_executions_status",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 1 AND 100",
            name="ck_agent_tool_executions_attempt_count",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_agent_tool_executions_error_code",
        ),
        CheckConstraint(
            "(status = 'IN_PROGRESS' AND result_task_id IS NULL AND "
            "result_summary IS NULL AND error_code IS NULL AND completed_at IS NULL) "
            "OR (status = 'COMPLETED' AND result_task_id IS NOT NULL AND "
            "result_summary IS NOT NULL AND error_code IS NULL AND "
            "completed_at IS NOT NULL) OR (status IN ('FAILED', 'UNKNOWN') AND "
            "result_task_id IS NULL AND result_summary IS NULL AND "
            "error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_agent_tool_executions_state",
        ),
        Index("ix_agent_tool_executions_run_id", "run_id"),
        Index("ix_agent_tool_executions_user_id", "user_id"),
        Index("ix_agent_tool_executions_user_status", "user_id", "status"),
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
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'IN_PROGRESS'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    result_task_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )
    result_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
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
