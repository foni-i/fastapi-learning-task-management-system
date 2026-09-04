"""Strict serializable state contracts for the Stage 9 Agent workflow."""

import json
from enum import StrEnum
from hashlib import sha256
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.agent.metrics import AgentRunMetrics
from app.agent.schemas import PlanningGoal, PlanningResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import PublicTask, TaskListResponse

MAX_PLAN_ACTIONS = 3
MAX_PLAN_REVISIONS = 2
MAX_SAFE_SUMMARY_LENGTH = 2_000
PLAN_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"

_SERVER_OWNED_ACTION_FIELDS = frozenset(
    {
        "user_id",
        "created_at",
        "updated_at",
        "completed_at",
        "session",
        "transaction",
        "commit",
        "rollback",
    }
)


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def _bounded_text(value: object, *, label: str, maximum: int) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    if len(normalized) > maximum:
        raise ValueError(f"{label} is too long")
    return normalized


class AgentContextKind(StrEnum):
    PROJECTS = "projects"
    TASKS = "tasks"


class AgentWriteToolName(StrEnum):
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"


class AgentApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUEST_CHANGES = "request_changes"


class AgentValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class AgentExecutionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentVerificationStatus(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    REJECTED = "rejected"
    NO_ACTION = "no_action"


class AgentTerminalStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class AgentGoalAnalysis(_FrozenContract):
    """Store transparent analysis without model reasoning or runtime identity."""

    objective: str = Field(min_length=1, max_length=2_000)
    constraints: tuple[str, ...] = Field(default=(), max_length=20)
    required_context: tuple[AgentContextKind, ...] = Field(
        min_length=1,
        max_length=2,
    )

    @field_validator("objective", mode="before")
    @classmethod
    def normalize_objective(cls, value: object) -> object:
        return _bounded_text(value, label="Analyzed objective", maximum=2_000)

    @field_validator("constraints", mode="before")
    @classmethod
    def normalize_constraints(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        return tuple(
            _bounded_text(item, label="Analyzed constraint", maximum=500)
            for item in value
        )

    @model_validator(mode="after")
    def require_unique_context(self) -> Self:
        if len(self.required_context) != len(set(self.required_context)):
            raise ValueError("Required context kinds must be unique")
        return self


class AgentContextSnapshot(_FrozenContract):
    """Keep only existing public owner-scoped list responses."""

    projects: ProjectListResponse
    tasks: TaskListResponse


class AgentProposedAction(_FrozenContract):
    """Represent one bounded write proposal that has not been authorized."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: AgentWriteToolName
    arguments: dict[str, JsonValue] = Field(max_length=20)

    @field_validator("arguments")
    @classmethod
    def reject_runtime_and_server_fields(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if _SERVER_OWNED_ACTION_FIELDS.intersection(value):
            raise ValueError("Proposed action contains a forbidden field")
        return value


class AgentPlanProposal(_FrozenContract):
    """Pair public planning output with zero to three proposed Task writes."""

    planning_result: PlanningResult
    actions: tuple[AgentProposedAction, ...] = Field(
        default=(),
        max_length=MAX_PLAN_ACTIONS,
    )

    @model_validator(mode="after")
    def require_unique_action_keys(self) -> Self:
        keys = [action.action_key for action in self.actions]
        if len(keys) != len(set(keys)):
            raise ValueError("Proposed action keys must be unique")
        return self


def fingerprint_plan_proposal(proposal: AgentPlanProposal) -> str:
    """Return a stable digest that binds validation and approval to one plan."""

    canonical = json.dumps(
        proposal.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class AgentPlanValidation(_FrozenContract):
    """Expose a stable deterministic validation result without diagnostics."""

    status: AgentValidationStatus
    revision: int = Field(default=0, ge=0, le=MAX_PLAN_REVISIONS)
    proposal_fingerprint: str | None = Field(
        default=None,
        pattern=PLAN_FINGERPRINT_PATTERN,
    )
    error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def require_consistent_error(self) -> Self:
        if self.status is AgentValidationStatus.VALID:
            if self.error_code is not None or self.proposal_fingerprint is None:
                raise ValueError(
                    "A valid plan requires a fingerprint and no error code"
                )
        elif self.error_code is None or self.proposal_fingerprint is not None:
            raise ValueError("An invalid plan requires only an error code")
        return self


class AgentActionExecutionRecord(_FrozenContract):
    """Record one safe action outcome without arguments or internal diagnostics."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: AgentWriteToolName
    outcome: AgentExecutionOutcome
    result: PublicTask | None = None
    error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def require_consistent_result(self) -> Self:
        if self.outcome is AgentExecutionOutcome.SUCCEEDED:
            if self.result is None or self.error_code is not None:
                raise ValueError("Successful execution requires only a public result")
        elif self.result is not None or self.error_code is None:
            raise ValueError("Failed execution requires only a safe error code")
        return self


class AgentVerification(_FrozenContract):
    """Carry the deterministic terminal verification classification."""

    status: AgentVerificationStatus
    checked_action_count: int = Field(ge=0, le=MAX_PLAN_ACTIONS)
    error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def require_consistent_error(self) -> Self:
        if self.status is AgentVerificationStatus.FAILED:
            if self.error_code is None:
                raise ValueError("Failed verification requires an error code")
        elif self.error_code is not None:
            raise ValueError("Successful verification cannot contain an error code")
        return self


class AgentGraphInput(_FrozenContract):
    """Accept only an already validated planning goal."""

    goal: PlanningGoal


class AgentGraphState(_FrozenContract):
    """Canonical serializable values exchanged as partial node updates."""

    goal: PlanningGoal
    analysis: AgentGoalAnalysis | None = None
    context: AgentContextSnapshot | None = None
    proposal: AgentPlanProposal | None = None
    validation: AgentPlanValidation | None = None
    approval_decision: AgentApprovalDecision | None = None
    approval_feedback: str | None = Field(default=None, max_length=1_000)
    approval_revision: int | None = Field(
        default=None,
        ge=0,
        le=MAX_PLAN_REVISIONS,
    )
    approval_proposal_fingerprint: str | None = Field(
        default=None,
        pattern=PLAN_FINGERPRINT_PATTERN,
    )
    revision_count: int = Field(default=0, ge=0, le=MAX_PLAN_REVISIONS)
    execution_records: tuple[AgentActionExecutionRecord, ...] = Field(
        default=(),
        max_length=MAX_PLAN_ACTIONS,
    )
    verification: AgentVerification | None = None
    terminal_status: AgentTerminalStatus | None = None
    workflow_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )
    summary: str | None = Field(default=None, max_length=MAX_SAFE_SUMMARY_LENGTH)
    metrics: AgentRunMetrics | None = None

    @field_validator("approval_feedback", "summary", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            return None
        maximum = 1_000 if info.field_name == "approval_feedback" else 2_000
        return _bounded_text(value, label="Agent state text", maximum=maximum)


class AgentGraphOutput(_FrozenContract):
    """Whitelist the eventual public result rather than exposing graph state."""

    status: AgentTerminalStatus
    plan: PlanningResult | None = None
    summary: str = Field(min_length=1, max_length=MAX_SAFE_SUMMARY_LENGTH)
    execution_records: tuple[AgentActionExecutionRecord, ...] = Field(
        default=(),
        max_length=MAX_PLAN_ACTIONS,
    )
    metrics: AgentRunMetrics | None = None

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: object) -> object:
        return _bounded_text(
            value,
            label="Agent output summary",
            maximum=MAX_SAFE_SUMMARY_LENGTH,
        )
