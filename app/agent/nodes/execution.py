"""Bounded sequential execution of validated and explicitly approved actions."""

from collections.abc import Mapping
from typing import Never, Protocol, TypedDict

from app.agent.context import AgentRuntimeContext
from app.agent.policy import is_high_impact_tool
from app.agent.state import (
    AgentActionExecutionRecord,
    AgentApprovalDecision,
    AgentExecutionOutcome,
    AgentGraphState,
    AgentProposedAction,
    AgentValidationStatus,
    fingerprint_plan_proposal,
)
from app.agent.tools import (
    AgentToolGateway,
    AgentToolResult,
    AgentWriteToolResult,
    execute_tool,
    validate_tool_arguments,
    validate_write_tool_result,
)
from app.core.exceptions import (
    ArchivedProjectError,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)

AGENT_EXECUTION_NOT_AUTHORIZED_MESSAGE = "Agent actions are not authorized"
AGENT_ACTION_FAILED = "AGENT_ACTION_FAILED"

_SAFE_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (TaskNotFoundError, "TASK_NOT_FOUND"),
    (ProjectNotFoundError, "PROJECT_NOT_FOUND"),
    (TaskDateOrderError, "TASK_DATE_ORDER_INVALID"),
    (TaskTransitionError, "TASK_TRANSITION_INVALID"),
    (ArchivedProjectError, "PROJECT_ARCHIVED"),
)


class AgentExecutionNotAuthorizedError(RuntimeError):
    """Reject writes when any independent authorization proof is absent."""


class ToolDispatcher(Protocol):
    """Make the allowlisted Tool boundary injectable for deterministic tests."""

    def __call__(
        self,
        name: str,
        arguments: Mapping[str, object],
        context: AgentRuntimeContext,
        *,
        gateway: AgentToolGateway | None = None,
    ) -> AgentToolResult: ...


class IdempotentActionExecutor(Protocol):
    """Execute one approved action through a durable idempotency boundary."""

    def __call__(
        self,
        action: AgentProposedAction,
        *,
        revision: int,
        proposal_fingerprint: str,
        runtime_context: AgentRuntimeContext,
    ) -> AgentWriteToolResult: ...


class AgentExecutionUpdate(TypedDict):
    execution_records: tuple[AgentActionExecutionRecord, ...]


def _fail_not_authorized() -> Never:
    raise AgentExecutionNotAuthorizedError(AGENT_EXECUTION_NOT_AUTHORIZED_MESSAGE)


def _require_current_authorization(state: AgentGraphState) -> None:
    proposal = state.proposal
    validation = state.validation
    if proposal is None or validation is None:
        _fail_not_authorized()
    fingerprint = fingerprint_plan_proposal(proposal)
    if (
        validation.status is not AgentValidationStatus.VALID
        or validation.revision != state.revision_count
        or validation.proposal_fingerprint != fingerprint
        or state.approval_decision is not AgentApprovalDecision.APPROVED
        or state.approval_revision != state.revision_count
        or state.approval_proposal_fingerprint != fingerprint
    ):
        _fail_not_authorized()


def _safe_error_code(exc: Exception) -> str:
    for exception_type, code in _SAFE_ERROR_CODES:
        if isinstance(exc, exception_type):
            return code
    return AGENT_ACTION_FAILED


def execute_tasks(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
    dispatcher: ToolDispatcher = execute_tool,
    gateway: AgentToolGateway | None = None,
    action_executor: IdempotentActionExecutor | None = None,
) -> AgentExecutionUpdate:
    """Execute each approved write once, stopping after the first failure."""

    _require_current_authorization(state)
    if not runtime_context.write_tools_enabled:
        _fail_not_authorized()
    proposal = state.proposal
    assert proposal is not None

    try:
        for action in proposal.actions:
            validate_tool_arguments(
                action.tool_name.value,
                action.arguments,
                runtime_context,
            )
            if is_high_impact_tool(action.tool_name.value) and action_executor is None:
                _fail_not_authorized()
    except Exception:
        _fail_not_authorized()

    records: list[AgentActionExecutionRecord] = []
    for action in proposal.actions:
        try:
            validate_tool_arguments(
                action.tool_name.value,
                action.arguments,
                runtime_context,
            )
            if action_executor is None:
                result = dispatcher(
                    action.tool_name.value,
                    action.arguments,
                    runtime_context,
                    gateway=gateway,
                )
            else:
                result = action_executor(
                    action,
                    revision=state.revision_count,
                    proposal_fingerprint=fingerprint_plan_proposal(proposal),
                    runtime_context=runtime_context,
                )
            public_result = validate_write_tool_result(
                action.tool_name.value,
                result,
            )
            records.append(
                AgentActionExecutionRecord(
                    action_key=action.action_key,
                    tool_name=action.tool_name,
                    outcome=AgentExecutionOutcome.SUCCEEDED,
                    result=public_result,
                )
            )
        except Exception as exc:
            records.append(
                AgentActionExecutionRecord(
                    action_key=action.action_key,
                    tool_name=action.tool_name,
                    outcome=AgentExecutionOutcome.FAILED,
                    error_code=_safe_error_code(exc),
                )
            )
            break
    return {"execution_records": tuple(records)}
