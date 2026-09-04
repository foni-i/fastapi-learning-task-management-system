"""Explicit approval boundary for proposed Agent write actions."""

from typing import Never, Protocol, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

AGENT_APPROVAL_UNAVAILABLE_MESSAGE = "Agent approval could not be obtained"
AGENT_APPROVAL_REQUIRED_MESSAGE = "Agent plan is not ready for approval"
PLAN_REVISION_LIMIT_MESSAGE = "Plan revision limit reached"


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
) -> AgentApprovalUpdate:
    """Request an explicit decision for exactly the current validated proposal."""

    fingerprint = _current_validated_fingerprint(state)
    proposal = state.proposal
    assert proposal is not None
    request = AgentApprovalRequest(
        plan_summary=proposal.planning_result.plan.summary,
        action_names=tuple(action.tool_name for action in proposal.actions),
        action_count=len(proposal.actions),
        revision=state.revision_count,
    )
    try:
        response = AgentApprovalResponse.model_validate(decider.decide(request))
    except Exception as exc:
        if isinstance(exc, AgentApprovalRequiredError):
            raise
        raise AgentApprovalUnavailableError(
            AGENT_APPROVAL_UNAVAILABLE_MESSAGE
        ) from None

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
