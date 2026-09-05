"""Strict product contracts for Agent thread, run, and approval records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.schemas import PlanningGoal
from app.models.agent_run import (
    AgentApprovalStatus,
    AgentRunStatus,
    AgentThreadStatus,
)

AGENT_RECORD_TIMESTAMP_MESSAGE = "Agent record timestamp must include timezone"


class AgentRunNode(StrEnum):
    ANALYZE_GOAL = "analyze_goal"
    LOAD_CONTEXT = "load_context"
    GENERATE_PLAN = "generate_plan"
    VALIDATE_PLAN = "validate_plan"
    REQUEST_APPROVAL = "request_approval"
    EXECUTE_TASKS = "execute_tasks"
    VERIFY_RESULT = "verify_result"
    SUMMARIZE = "summarize"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(AGENT_RECORD_TIMESTAMP_MESSAGE)
    return value.astimezone(UTC)


def _trim(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class _PublicRecord(_StrictContract):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        hide_input_in_errors=True,
    )


class AgentThreadCreate(_StrictContract):
    """Accept only the bounded safe summary used to create a product thread."""

    goal_summary: str = Field(min_length=1, max_length=2000)

    @field_validator("goal_summary", mode="before")
    @classmethod
    def normalize_goal_summary(cls, value: object) -> object:
        return _trim(value)


class AgentRunStartRequest(_StrictContract):
    """Accept one bounded goal without client-selected runtime identity."""

    goal: PlanningGoal


class AgentApprovalSubmissionDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class AgentApprovalSubmission(_StrictContract):
    """Bind one human decision to an exact pending proposal revision."""

    revision: int = Field(ge=0, le=2)
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: AgentApprovalSubmissionDecision
    feedback: str | None = Field(default=None, max_length=1000)

    @field_validator("feedback", mode="before")
    @classmethod
    def normalize_feedback(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Approval feedback must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_feedback_contract(self) -> Self:
        if self.decision is AgentApprovalSubmissionDecision.REQUEST_CHANGES:
            if self.feedback is None:
                raise ValueError("Requested changes require feedback")
        elif self.feedback is not None:
            raise ValueError("Feedback is only allowed for requested changes")
        return self


class AgentRunMetricsSnapshot(_StrictContract):
    """Expose explicit safe aggregate counters instead of arbitrary JSON."""

    model_round_count: int = Field(default=0, ge=0)
    provider_attempt_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_consistent_token_total(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("Agent token metrics are inconsistent")
        return self


class _FlatAgentRunRecord(Protocol):
    id: UUID
    thread_id: UUID
    status: str
    current_node: str | None
    summary: str | None
    error_code: str | None
    prompt_version: str
    model_round_count: int
    provider_attempt_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    created_at: datetime
    updated_at: datetime


class PublicAgentThread(_PublicRecord):
    """Whitelist one owner-visible product thread without its owner column."""

    id: UUID
    goal_summary: str = Field(min_length=1, max_length=2000)
    status: AgentThreadStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class PublicAgentRun(_PublicRecord):
    """Whitelist safe execution status and explicit aggregate metrics."""

    id: UUID
    thread_id: UUID
    status: AgentRunStatus
    current_node: AgentRunNode | None
    summary: str | None = Field(max_length=2000)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    prompt_version: str = Field(min_length=1, max_length=64)
    metrics: AgentRunMetricsSnapshot
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def collect_explicit_orm_metrics(cls, value: object) -> object:
        """Map flat ORM metric columns into the public nested whitelist."""

        if isinstance(value, dict) or hasattr(value, "metrics"):
            return value
        if not hasattr(value, "model_round_count"):
            return value
        record = cast(_FlatAgentRunRecord, value)
        return {
            "id": record.id,
            "thread_id": record.thread_id,
            "status": record.status,
            "current_node": record.current_node,
            "summary": record.summary,
            "error_code": record.error_code,
            "prompt_version": record.prompt_version,
            "metrics": {
                "model_round_count": record.model_round_count,
                "provider_attempt_count": record.provider_attempt_count,
                "tool_call_count": record.tool_call_count,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "total_tokens": record.total_tokens,
                "latency_ms": record.latency_ms,
            },
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @field_validator("prompt_version", mode="before")
    @classmethod
    def normalize_prompt_version(cls, value: object) -> object:
        return _trim(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class AgentThreadRunResult(_StrictContract):
    """Return a newly created thread and its first run as public records."""

    thread: PublicAgentThread
    run: PublicAgentRun


class PublicAgentApproval(_PublicRecord):
    """Whitelist one exact proposal decision without model or tool payloads."""

    id: UUID
    run_id: UUID
    revision: int = Field(ge=0, le=2)
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: AgentApprovalStatus
    feedback: str | None = Field(default=None, max_length=1000)
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("feedback", mode="before")
    @classmethod
    def normalize_feedback(cls, value: object) -> object:
        if value is None:
            return None
        normalized = _trim(value)
        return normalized or None

    @field_validator("decided_at", "created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def require_consistent_decision_state(self) -> Self:
        if self.decision is AgentApprovalStatus.PENDING:
            if self.decided_at is not None or self.feedback is not None:
                raise ValueError("Pending approval cannot contain a decision result")
        elif self.decided_at is None:
            raise ValueError("Decided approval requires a decision timestamp")
        elif self.decision is AgentApprovalStatus.REQUEST_CHANGES:
            if self.feedback is None:
                raise ValueError("Requested changes require feedback")
        elif self.feedback is not None:
            raise ValueError("Only requested changes can contain feedback")
        return self


class AgentRunSnapshot(_StrictContract):
    """Expose only product records needed to observe one Agent run."""

    thread: PublicAgentThread
    run: PublicAgentRun
    approval: PublicAgentApproval | None = None


class AgentRunErrorResponse(_StrictContract):
    """Document one fixed safe Agent HTTP error without diagnostics."""

    detail: str
