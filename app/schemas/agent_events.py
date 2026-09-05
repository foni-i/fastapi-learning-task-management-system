"""Strict public contracts for persisted Agent progress events."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.agent_run import AgentRunStatus
from app.models.agent_tool_execution import AgentToolExecutionStatus
from app.schemas.agent_run import AgentRunMetricsSnapshot, AgentRunNode

AGENT_EVENT_VERSION = "agent-event.v1"
AGENT_EVENT_SUMMARY_MAX_LENGTH = 300


class PublicAgentEventType(StrEnum):
    RUN_STATUS = "run_status"
    NODE_STATUS = "node_status"
    TOOL_STARTED = "tool_started"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    METRICS = "metrics"
    HEARTBEAT = "heartbeat"
    TERMINAL_RESULT = "terminal_result"
    SAFE_ERROR = "safe_error"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class RunStatusPayload(_Payload):
    kind: Literal[PublicAgentEventType.RUN_STATUS] = PublicAgentEventType.RUN_STATUS
    status: AgentRunStatus


class NodeStatusPayload(_Payload):
    kind: Literal[PublicAgentEventType.NODE_STATUS] = PublicAgentEventType.NODE_STATUS
    node: AgentRunNode


class ToolStartedPayload(_Payload):
    kind: Literal[PublicAgentEventType.TOOL_STARTED] = PublicAgentEventType.TOOL_STARTED
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class ToolResultPayload(_Payload):
    kind: Literal[PublicAgentEventType.TOOL_RESULT] = PublicAgentEventType.TOOL_RESULT
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: AgentToolExecutionStatus
    summary: str = Field(min_length=1, max_length=AGENT_EVENT_SUMMARY_MAX_LENGTH)


class ApprovalRequiredPayload(_Payload):
    kind: Literal[PublicAgentEventType.APPROVAL_REQUIRED] = (
        PublicAgentEventType.APPROVAL_REQUIRED
    )
    revision: int = Field(ge=0, le=2)
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class MetricsPayload(_Payload):
    kind: Literal[PublicAgentEventType.METRICS] = PublicAgentEventType.METRICS
    metrics: AgentRunMetricsSnapshot


class HeartbeatPayload(_Payload):
    kind: Literal[PublicAgentEventType.HEARTBEAT] = PublicAgentEventType.HEARTBEAT


class TerminalResultPayload(_Payload):
    kind: Literal[PublicAgentEventType.TERMINAL_RESULT] = (
        PublicAgentEventType.TERMINAL_RESULT
    )
    status: AgentRunStatus
    summary: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_terminal_status(self) -> Self:
        if self.status not in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.REJECTED,
            AgentRunStatus.PARTIAL_FAILURE,
            AgentRunStatus.FAILED,
        }:
            raise ValueError("Terminal event requires a terminal run status")
        return self


class SafeErrorPayload(_Payload):
    kind: Literal[PublicAgentEventType.SAFE_ERROR] = PublicAgentEventType.SAFE_ERROR
    error_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


type PublicAgentEventPayload = Annotated[
    RunStatusPayload
    | NodeStatusPayload
    | ToolStartedPayload
    | ToolResultPayload
    | ApprovalRequiredPayload
    | MetricsPayload
    | HeartbeatPayload
    | TerminalResultPayload
    | SafeErrorPayload,
    Field(discriminator="kind"),
]


class PublicAgentEvent(BaseModel):
    """Expose one stable event without internal graph or persistence state."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal["agent-event.v1"] = "agent-event.v1"
    event_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]{8}$"
    )
    sequence: int = Field(ge=1)
    run_id: UUID
    event_type: PublicAgentEventType
    occurred_at: datetime
    payload: PublicAgentEventPayload

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Agent event timestamp must include timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def match_payload_type(self) -> Self:
        if self.event_type is not self.payload.kind:
            raise ValueError("Agent event type must match its public payload")
        expected_id = f"{self.run_id}:{self.sequence:08d}"
        if self.event_id != expected_id:
            raise ValueError("Agent event ID must match run and sequence")
        return self
