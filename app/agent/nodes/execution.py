"""Bounded sequential execution of validated and explicitly approved actions."""

from collections.abc import Mapping
from typing import Never, Protocol, TypedDict

from app.agent.context import AgentRuntimeContext
from app.agent.state import (
    AgentActionExecutionRecord,
    AgentApprovalDecision,
    AgentExecutionOutcome,
    AgentGraphState,
    AgentValidationStatus,
    fingerprint_plan_proposal,
)
from app.agent.tools import (
    AgentToolGateway,
    AgentToolResult,
    execute_tool,
    validate_tool_arguments,
)
from app.core.exceptions import (
    ArchivedProjectError,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.schemas.task import PublicTask

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
            result = dispatcher(
                action.tool_name.value,
                action.arguments,
                runtime_context,
                gateway=gateway,
            )
            public_result = PublicTask.model_validate(result)
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
