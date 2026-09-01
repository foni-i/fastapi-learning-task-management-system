"""Task persistence model and fixed Stage 7 storage contract."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Task(Base):
    """Persist one user-owned Task under an equally owned Project."""

    __tablename__ = "tasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_tasks"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_tasks_user_id_users"),
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["projects.id", "projects.user_id"],
            name="fk_tasks_project_id_user_id_projects",
        ),
        CheckConstraint("btrim(title) <> ''", name="ck_tasks_title_not_blank"),
        CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')",
            name="ck_tasks_priority",
        ),
        CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 1440",
            name="ck_tasks_estimated_minutes",
        ),
        CheckConstraint(
            "due_at IS NULL OR planned_date IS NULL OR "
            "due_at >= (planned_date::timestamp AT TIME ZONE 'UTC')",
            name="ck_tasks_due_at_not_before_planned_date",
        ),
        CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="ck_tasks_completed_at_matches_status",
        ),
        Index("ix_tasks_user_id", "user_id"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_priority", "user_id", "priority"),
        Index("ix_tasks_user_due_at", "user_id", "due_at"),
        Index("ix_tasks_user_created_at_id", "user_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'TODO'")
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'MEDIUM'")
    )
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
