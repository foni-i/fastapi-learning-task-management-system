"""Explicit approval boundary for proposed Agent write actions."""

import json
from typing import Annotated, Literal, Never, Protocol, Self, TypedDict
from uuid import UUID

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.context import AgentRuntimeContext
from app.agent.schemas import PlanningResult
from app.agent.state import (
    MAX_PLAN_ACTIONS,
    MAX_PLAN_REVISIONS,
    AgentApprovalDecision,
    AgentGraphState,
    AgentPlanProposal,
    AgentTerminalStatus,
    AgentValidationStatus,
    AgentWriteToolName,
    fingerprint_plan_proposal,
)
from app.agent.tools import (
    BatchCreateTasksToolArguments,
    CreateTaskToolArguments,
    DeleteTaskToolArguments,
    UpdateTaskToolArguments,
    validate_tool_arguments,
)
from app.schemas.task import TaskCreate, TaskUpdate

AGENT_APPROVAL_UNAVAILABLE_MESSAGE = "Agent approval could not be obtained"
AGENT_APPROVAL_REQUIRED_MESSAGE = "Agent plan is not ready for approval"
AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE = "Agent approval preview is invalid"
PLAN_REVISION_LIMIT_MESSAGE = "Plan revision limit reached"
MAX_APPROVAL_PREVIEW_BYTES = 65_536
_RUN_ID_ENVELOPE_BYTES = len(f',"run_id":"{UUID(int=0)}"'.encode())


class _ApprovalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AgentApprovalRequest(_ApprovalContract):
    """Expose only the bounded plan summary and action names to an approver."""

    plan_summary: str = Field(min_length=1, max_length=1_000)
    action_names: tuple[AgentWriteToolName, ...] = Field(max_length=MAX_PLAN_ACTIONS)
    action_count: int = Field(ge=0, le=MAX_PLAN_ACTIONS)
    revision: int = Field(ge=0, le=MAX_PLAN_REVISIONS)

    @field_validator("plan_summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Approval summary must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_matching_count(self) -> Self:
        if self.action_count != len(self.action_names):
            raise ValueError("Approval action count is inconsistent")
        return self


class CreateTaskApprovalPreview(_ApprovalContract):
    """Expose one exact validated create payload without trusted identity."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: Literal[AgentWriteToolName.CREATE_TASK]
    task: TaskCreate


class UpdateTaskApprovalPreview(_ApprovalContract):
    """Expose one locator and only the update fields supplied by the proposal."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: Literal[AgentWriteToolName.UPDATE_TASK]
    task_id: UUID
    changes: TaskUpdate


class BatchCreateTasksApprovalPreview(_ApprovalContract):
    """Expose one bounded same-project create batch."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: Literal[AgentWriteToolName.BATCH_CREATE_TASKS]
    tasks: tuple[TaskCreate, ...] = Field(min_length=1, max_length=10)


class DeleteTaskApprovalPreview(_ApprovalContract):
    """Expose only the owner-scoped Task locator proposed for deletion."""

    action_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: Literal[AgentWriteToolName.DELETE_TASK]
    task_id: UUID


type AgentApprovalPreviewAction = Annotated[
    CreateTaskApprovalPreview
    | UpdateTaskApprovalPreview
    | BatchCreateTasksApprovalPreview
    | DeleteTaskApprovalPreview,
    Field(discriminator="tool_name"),
]


def _task_arguments(task: TaskCreate | TaskUpdate) -> dict[str, object]:
    return task.model_dump(mode="json", exclude_unset=True)


class AgentApprovalProposalPreview(_ApprovalContract):
    """Carry the complete bounded public proposal at the approval boundary."""

    revision: int = Field(ge=0, le=MAX_PLAN_REVISIONS)
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_result: PlanningResult
    actions: tuple[AgentApprovalPreviewAction, ...] = Field(
        default=(),
        max_length=MAX_PLAN_ACTIONS,
    )

    def to_plan_proposal(self) -> AgentPlanProposal:
        """Losslessly rebuild the fingerprint input without adding defaults."""

        actions = []
        for action in self.actions:
            if isinstance(action, CreateTaskApprovalPreview):
                arguments = _task_arguments(action.task)
            elif isinstance(action, UpdateTaskApprovalPreview):
                arguments = {
                    "task_id": str(action.task_id),
                    **_task_arguments(action.changes),
                }
            elif isinstance(action, BatchCreateTasksApprovalPreview):
                arguments = {"tasks": [_task_arguments(task) for task in action.tasks]}
            else:
                arguments = {"task_id": str(action.task_id)}
            actions.append(
                {
                    "action_key": action.action_key,
                    "tool_name": action.tool_name,
                    "arguments": arguments,
                }
            )
        return AgentPlanProposal.model_validate(
            {
                "planning_result": self.planning_result,
                "actions": actions,
            }
        )

    def canonical_json(self) -> str:
        """Serialize exactly the public fields used for the byte ceiling."""

        return json.dumps(
            self.model_dump(mode="json", exclude_unset=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @model_validator(mode="after")
    def require_exact_bounded_proposal(self) -> Self:
        proposal = self.to_plan_proposal()
        if fingerprint_plan_proposal(proposal) != self.proposal_fingerprint:
            raise ValueError(AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE)
        byte_limit = MAX_APPROVAL_PREVIEW_BYTES
        if "run_id" not in type(self).model_fields:
            byte_limit -= _RUN_ID_ENVELOPE_BYTES
        if len(self.canonical_json().encode("utf-8")) > byte_limit:
            raise ValueError(AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE)
        return self


class AgentApprovalInterrupt(AgentApprovalRequest):
    """Expose one exact validated proposal at the durable human boundary."""

    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: AgentApprovalProposalPreview

    @model_validator(mode="after")
    def require_matching_preview(self) -> Self:
        if (
            self.revision != self.preview.revision
            or self.proposal_fingerprint != self.preview.proposal_fingerprint
            or self.plan_summary != self.preview.planning_result.plan.summary
            or self.action_names
            != tuple(action.tool_name for action in self.preview.actions)
        ):
            raise ValueError(AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE)
        return self


class AgentApprovalResponse(_ApprovalContract):
    """Capture one explicit decision without accepting arbitrary payloads."""

    decision: AgentApprovalDecision
    feedback: str | None = Field(default=None, max_length=1_000)

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
    def require_decision_feedback_contract(self) -> Self:
        if self.decision is AgentApprovalDecision.REQUEST_CHANGES:
            if self.feedback is None:
                raise ValueError("Requested changes require feedback")
        elif self.feedback is not None:
            raise ValueError("Feedback is only allowed for requested changes")
        return self


class ApprovalDecider(Protocol):
    """Adapter boundary for a future HTTP or persisted approval mechanism."""

    def decide(self, request: AgentApprovalRequest) -> AgentApprovalResponse: ...


class AgentApprovalUnavailableError(RuntimeError):
    """Hide approval adapter failures and untrusted response details."""


class AgentApprovalRequiredError(RuntimeError):
    """Reject approval when the current proposal has not been validated."""


class AgentApprovalUpdate(TypedDict, total=False):
    approval_decision: AgentApprovalDecision
    approval_feedback: str | None
    approval_revision: int | None
    approval_proposal_fingerprint: str | None
    revision_count: int
    proposal: AgentPlanProposal | None
    validation: None
    terminal_status: AgentTerminalStatus
    summary: str


def _fail_required() -> Never:
    raise AgentApprovalRequiredError(AGENT_APPROVAL_REQUIRED_MESSAGE)


def _current_validated_fingerprint(state: AgentGraphState) -> str:
    proposal = state.proposal
    validation = state.validation
    if proposal is None or validation is None:
        _fail_required()
    fingerprint = fingerprint_plan_proposal(proposal)
    if (
        validation.status is not AgentValidationStatus.VALID
        or validation.revision != state.revision_count
        or validation.proposal_fingerprint != fingerprint
    ):
        _fail_required()
    return fingerprint


def request_approval(
    state: AgentGraphState,
    *,
    decider: ApprovalDecider,
    runtime_context: AgentRuntimeContext,
) -> AgentApprovalUpdate:
    """Request an explicit decision for exactly the current validated proposal."""

    request, fingerprint = _build_approval_request(
        state,
        runtime_context=runtime_context,
    )
    try:
        response = AgentApprovalResponse.model_validate(decider.decide(request))
    except Exception as exc:
        if isinstance(exc, AgentApprovalRequiredError):
            raise
        raise AgentApprovalUnavailableError(
            AGENT_APPROVAL_UNAVAILABLE_MESSAGE
        ) from None
    return _apply_approval_response(state, response, fingerprint)


def _build_approval_request(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
) -> tuple[AgentApprovalInterrupt, str]:
    fingerprint = _current_validated_fingerprint(state)
    proposal = state.proposal
    assert proposal is not None
    try:
        preview_actions: list[AgentApprovalPreviewAction] = []
        for action in proposal.actions:
            validated = validate_tool_arguments(
                action.tool_name.value,
                action.arguments,
                runtime_context,
            )
            if isinstance(validated, CreateTaskToolArguments):
                preview_action: AgentApprovalPreviewAction = CreateTaskApprovalPreview(
                    action_key=action.action_key,
                    tool_name=AgentWriteToolName.CREATE_TASK,
                    task=TaskCreate.model_validate(action.arguments),
                )
            elif isinstance(validated, UpdateTaskToolArguments):
                preview_action = UpdateTaskApprovalPreview(
                    action_key=action.action_key,
                    tool_name=AgentWriteToolName.UPDATE_TASK,
                    task_id=validated.task_id,
                    changes=validated.to_task_update(),
                )
            elif isinstance(validated, BatchCreateTasksToolArguments):
                preview_action = BatchCreateTasksApprovalPreview(
                    action_key=action.action_key,
                    tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
                    tasks=validated.tasks,
                )
            elif isinstance(validated, DeleteTaskToolArguments):
                preview_action = DeleteTaskApprovalPreview(
                    action_key=action.action_key,
                    tool_name=AgentWriteToolName.DELETE_TASK,
                    task_id=validated.task_id,
                )
            else:
                raise ValueError(AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE)
            preview_actions.append(preview_action)

        preview = AgentApprovalProposalPreview(
            revision=state.revision_count,
            proposal_fingerprint=fingerprint,
            planning_result=proposal.planning_result,
            actions=tuple(preview_actions),
        )
        request = AgentApprovalInterrupt(
            plan_summary=proposal.planning_result.plan.summary,
            action_names=tuple(action.tool_name for action in proposal.actions),
            action_count=len(proposal.actions),
            revision=state.revision_count,
            proposal_fingerprint=fingerprint,
            preview=preview,
        )
    except Exception:
        raise AgentApprovalRequiredError(
            AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE
        ) from None
    return request, fingerprint


def _apply_approval_response(
    state: AgentGraphState,
    response: AgentApprovalResponse,
    fingerprint: str,
) -> AgentApprovalUpdate:
    if response.decision is AgentApprovalDecision.REQUEST_CHANGES:
        if state.revision_count >= MAX_PLAN_REVISIONS:
            return {
                "approval_decision": AgentApprovalDecision.REJECTED,
                "approval_feedback": None,
                "approval_revision": state.revision_count,
                "approval_proposal_fingerprint": fingerprint,
                "terminal_status": AgentTerminalStatus.REJECTED,
                "summary": PLAN_REVISION_LIMIT_MESSAGE,
            }
        return {
            "approval_decision": AgentApprovalDecision.REQUEST_CHANGES,
            "approval_feedback": response.feedback,
            "approval_revision": None,
            "approval_proposal_fingerprint": None,
            "revision_count": state.revision_count + 1,
            "proposal": None,
            "validation": None,
        }

    terminal_update: AgentApprovalUpdate = {
        "approval_decision": response.decision,
        "approval_feedback": None,
        "approval_revision": state.revision_count,
        "approval_proposal_fingerprint": fingerprint,
    }
    if response.decision is AgentApprovalDecision.REJECTED:
        terminal_update["terminal_status"] = AgentTerminalStatus.REJECTED
    return terminal_update


def interrupt_for_approval(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
) -> AgentApprovalUpdate:
    """Pause durably and validate the exact decision supplied on resume."""

    request, fingerprint = _build_approval_request(
        state,
        runtime_context=runtime_context,
    )
    response = AgentApprovalResponse.model_validate(
        interrupt(request.model_dump(mode="json", exclude_unset=True))
    )
    return _apply_approval_response(state, response, fingerprint)
