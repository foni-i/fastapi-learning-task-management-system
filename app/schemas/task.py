"""Strict Task request, query, and public response contracts."""

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import TaskDateOrderError
from app.models.task import TaskPriority, TaskStatus

TASK_DATE_ORDER_MESSAGE = "Task due time cannot precede planned date"
TASK_UPDATE_EMPTY_MESSAGE = "At least one task field must be provided"
TASK_COMPLETION_ACTION_MESSAGE = "Completed status requires the completion action"
TASK_TIMESTAMP_MESSAGE = "Task timestamp must include timezone information"


class TaskSortField(StrEnum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    DUE_AT = "due_at"
    PLANNED_DATE = "planned_date"
    TITLE = "title"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


def _trim_title(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _normalize_description(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(TASK_TIMESTAMP_MESSAGE)
    return value.astimezone(UTC)


def validate_task_date_order(
    planned_date: date | None, due_at: datetime | None
) -> None:
    if planned_date is None or due_at is None:
        return
    planned_start = datetime.combine(planned_date, time.min, tzinfo=UTC)
    if due_at < planned_start:
        raise TaskDateOrderError(TASK_DATE_ORDER_MESSAGE)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    project_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    planned_date: date | None = None
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    priority: TaskPriority = TaskPriority.MEDIUM

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        return _trim_title(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        return _normalize_description(value)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        validate_task_date_order(self.planned_date, self.due_at)
        return self


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    planned_date: date | None = None
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    priority: TaskPriority | None = None
    status: TaskStatus | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_required_title(cls, value: object) -> object:
        if value is None:
            raise ValueError("Task title cannot be null")
        return _trim_title(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        return _normalize_description(value)

    @field_validator("priority", "status", mode="before")
    @classmethod
    def reject_null_required_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("Task priority and status cannot be null")
        return value

    @field_validator("status")
    @classmethod
    def reserve_completion_action(cls, value: TaskStatus) -> TaskStatus:
        if value is TaskStatus.COMPLETED:
            raise ValueError(TASK_COMPLETION_ACTION_MESSAGE)
        return value

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def validate_partial_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError(TASK_UPDATE_EMPTY_MESSAGE)
        if {"planned_date", "due_at"} <= self.model_fields_set:
            validate_task_date_order(self.planned_date, self.due_at)
        return self


class TaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    project_id: UUID | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    planned_from: date | None = None
    planned_to: date | None = None
    due_from: datetime | None = None
    due_to: datetime | None = None
    overdue: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    sort_by: TaskSortField = TaskSortField.CREATED_AT
    sort_direction: SortDirection = SortDirection.DESC

    @field_validator("title", mode="before")
    @classmethod
    def normalize_search(cls, value: object) -> object:
        return _trim_title(value)

    @field_validator("due_from", "due_to")
    @classmethod
    def normalize_due_range(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (
            self.planned_from
            and self.planned_to
            and self.planned_from > self.planned_to
        ):
            raise ValueError("Planned date range is invalid")
        if self.due_from and self.due_to and self.due_from > self.due_to:
            raise ValueError("Due time range is invalid")
        return self


class PublicTask(BaseModel):
    model_config = ConfigDict(
        extra="forbid", from_attributes=True, hide_input_in_errors=True
    )

    id: UUID
    project_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    planned_date: date | None
    due_at: datetime | None
    estimated_minutes: int | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("due_at", "completed_at", "created_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if (self.status is TaskStatus.COMPLETED) != (self.completed_at is not None):
            raise ValueError("Task completion state is inconsistent")
        return self


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PublicTask]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        expected = (
            0
            if self.total == 0
            else (self.total + self.page_size - 1) // self.page_size
        )
        if self.pages != expected or len(self.items) > self.page_size:
            raise ValueError("Task page metadata is inconsistent")
        return self


class TaskErrorResponse(BaseModel):
    """Document the fixed route-local safe Task error shape."""

    model_config = ConfigDict(extra="forbid")

    detail: str
