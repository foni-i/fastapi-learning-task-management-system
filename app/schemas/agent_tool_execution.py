"""Safe public projection of an Agent Tool execution audit record."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agent_tool_execution import AgentToolExecutionStatus


class PublicAgentToolExecution(BaseModel):
    """Expose bounded outcome metadata without owner identity or Tool arguments."""

    model_config = ConfigDict(
        extra="forbid", from_attributes=True, hide_input_in_errors=True
    )

    id: UUID
    run_id: UUID
    revision: int = Field(ge=0, le=2)
    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: str = Field(
        pattern=(r"^(create_task|update_task|batch_create_tasks|delete_task)$")
    )
    status: AgentToolExecutionStatus
    attempt_count: int = Field(ge=1, le=100)
    result_task_id: UUID | None
    result_summary: str | None = Field(default=None, max_length=300)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("started_at", "completed_at", "created_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Agent Tool execution timestamp requires timezone")
        return value.astimezone(UTC)
