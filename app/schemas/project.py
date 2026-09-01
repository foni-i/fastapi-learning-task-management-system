"""Strict Project request and public response contracts."""

from datetime import UTC, date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import ProjectDateOrderError
from app.models.project import ProjectStatus

PROJECT_NAME_ERROR_MESSAGE = "Project name must contain between 1 and 200 characters"
PROJECT_DESCRIPTION_ERROR_MESSAGE = "Project description cannot exceed 2000 characters"
PROJECT_DATE_ORDER_ERROR_MESSAGE = "Target date cannot precede start date"
PROJECT_UPDATE_EMPTY_ERROR_MESSAGE = "At least one project field must be provided"
PROJECT_ARCHIVE_ACTION_ERROR_MESSAGE = "Archived status requires the archive action"
PROJECT_TIMESTAMP_ERROR_MESSAGE = "Timestamp must include timezone information"


def validate_project_date_order(
    start_date: date | None,
    target_date: date | None,
) -> None:
    """Reject a target date before a simultaneously effective start date."""

    if start_date is not None and target_date is not None and target_date < start_date:
        raise ProjectDateOrderError(PROJECT_DATE_ORDER_ERROR_MESSAGE)


def _trim_name(value: object) -> object:
    """Trim a string name before its declared bounds are checked."""

    return value.strip() if isinstance(value, str) else value


def _normalize_description(value: object) -> object:
    """Trim optional description and canonicalize blank text to null."""

    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


class ProjectCreate(BaseModel):
    """Accept only user-editable fields for a new Project."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    target_date: date | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        """Store the bounded canonical project name."""

        return _trim_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        """Store null for absent or whitespace-only optional text."""

        return _normalize_description(value)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        """Check the complete create-time date pair."""

        validate_project_date_order(self.start_date, self.target_date)
        return self


class ProjectUpdate(BaseModel):
    """Represent a strict partial update while retaining missing fields."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    target_date: date | None = None
    status: ProjectStatus | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_and_trim_name(cls, value: object) -> object:
        """Allow omission but reject an explicit null project name."""

        if value is None:
            raise ValueError(PROJECT_NAME_ERROR_MESSAGE)
        return _trim_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        """Allow explicit null and canonicalize blank optional text."""

        return _normalize_description(value)

    @field_validator("status", mode="before")
    @classmethod
    def reject_null_status(cls, value: object) -> object:
        """Allow omission but reject an explicit null lifecycle value."""

        if value is None:
            raise ValueError("Project status cannot be null")
        return value

    @field_validator("status")
    @classmethod
    def reject_direct_archive(cls, value: ProjectStatus) -> ProjectStatus:
        """Reserve ARCHIVED for the dedicated idempotent action."""

        if value is ProjectStatus.ARCHIVED:
            raise ValueError(PROJECT_ARCHIVE_ACTION_ERROR_MESSAGE)
        return value

    @model_validator(mode="after")
    def validate_partial_update(self) -> Self:
        """Reject empty updates and validate a complete supplied date pair."""

        if not self.model_fields_set:
            raise ValueError(PROJECT_UPDATE_EMPTY_ERROR_MESSAGE)
        if {"start_date", "target_date"} <= self.model_fields_set:
            validate_project_date_order(self.start_date, self.target_date)
        return self


class PublicProject(BaseModel):
    """Expose only the stable public Project allowlist."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        hide_input_in_errors=True,
    )

    id: UUID
    name: str
    description: str | None
    start_date: date | None
    target_date: date | None
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject naive timestamps and normalize aware values to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(PROJECT_TIMESTAMP_ERROR_MESSAGE)
        return value.astimezone(UTC)


class ProjectListResponse(BaseModel):
    """Return fixed page metadata with public Project items."""

    model_config = ConfigDict(extra="forbid")

    items: list[PublicProject]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_page_metadata(self) -> Self:
        """Keep page count and item count consistent with the contract."""

        expected_pages = (
            0
            if self.total == 0
            else (self.total + self.page_size - 1) // self.page_size
        )
        if self.pages != expected_pages:
            raise ValueError("Project page metadata is inconsistent")
        if len(self.items) > self.page_size:
            raise ValueError("Project page contains too many items")
        return self


class ProjectErrorResponse(BaseModel):
    """Document the current route-local safe Project error shape."""

    model_config = ConfigDict(extra="forbid")

    detail: str
