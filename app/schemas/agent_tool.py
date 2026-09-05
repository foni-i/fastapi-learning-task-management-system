"""Bounded public results for high-impact internal Agent Tools."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentToolMutationResult(BaseModel):
    """Summarize one accepted mutation without exposing its input payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    operation: Literal["batch_create_tasks", "delete_task"]
    reference_task_id: UUID
    affected_count: int = Field(ge=1, le=10)
    summary: str = Field(min_length=1, max_length=100)
