"""Deterministic verification and public output for the Agent workflow."""

from typing import Never, TypedDict

from app.agent.state import (
    AgentApprovalDecision,
    AgentExecutionOutcome,
    AgentGraphOutput,
    AgentGraphState,
    AgentTerminalStatus,
    AgentValidationStatus,
    AgentVerification,
    AgentVerificationStatus,
    fingerprint_plan_proposal,
)

AGENT_RESULT_INVALID = "AGENT_RESULT_INVALID"
AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
AGENT_WORKFLOW_FAILED = "AGENT_WORKFLOW_FAILED"
AGENT_FINALIZATION_REQUIRED_MESSAGE = "Agent result has not been verified"

_SUMMARY_NO_ACTION = "Plan verified; no task changes were required."
_SUMMARY_SUCCEEDED = "Plan verified; {count} task action(s) completed."
_SUMMARY_REJECTED = "Plan was rejected; no task actions were executed."
_SUMMARY_PARTIAL = (
    "Plan partially completed; {succeeded} of {total} task actions succeeded."
)
_SUMMARY_FAILED = "Plan execution failed safely."
_SUMMARY_INVALID = "Agent result verification failed."


class AgentFinalizationError(RuntimeError):
    """Reject output creation from state that has not been verified."""


class AgentVerificationUpdate(TypedDict):
    verification: AgentVerification
    terminal_status: AgentTerminalStatus


def _invalid(checked_action_count: int) -> AgentVerificationUpdate:
    return {
        "verification": AgentVerification(
            status=AgentVerificationStatus.FAILED,
            checked_action_count=checked_action_count,
            error_code=AGENT_RESULT_INVALID,
        ),
        "terminal_status": AgentTerminalStatus.FAILED,
    }


def _has_current_plan_binding(state: AgentGraphState) -> bool:
    proposal = state.proposal
    validation = state.validation
    if proposal is None or validation is None:
        return False
    fingerprint = fingerprint_plan_proposal(proposal)
    return (
        validation.status is AgentValidationStatus.VALID
        and validation.revision == state.revision_count
        and validation.proposal_fingerprint == fingerprint
        and state.approval_revision == state.revision_count
        and state.approval_proposal_fingerprint == fingerprint
    )


def verify_result(state: AgentGraphState) -> AgentVerificationUpdate:
    """Classify only records consistent with the current approved proposal."""

    if state.workflow_error_code is not None:
        return {
            "verification": AgentVerification(
                status=AgentVerificationStatus.FAILED,
                checked_action_count=len(state.execution_records),
                error_code=AGENT_WORKFLOW_FAILED,
            ),
            "terminal_status": AgentTerminalStatus.FAILED,
        }
    if not _has_current_plan_binding(state):
        return _invalid(0)
    if state.approval_decision is AgentApprovalDecision.REJECTED:
        if state.execution_records:
            return _invalid(len(state.execution_records))
        return {
            "verification": AgentVerification(
                status=AgentVerificationStatus.REJECTED,
                checked_action_count=0,
            ),
            "terminal_status": AgentTerminalStatus.REJECTED,
        }
    if state.approval_decision is not AgentApprovalDecision.APPROVED:
        return _invalid(0)

    proposal = state.proposal
    assert proposal is not None
    actions = proposal.actions
    records = state.execution_records
    if len(records) > len(actions):
        return _invalid(len(records))
    if len({record.action_key for record in records}) != len(records):
        return _invalid(len(records))
    for action, record in zip(actions, records, strict=False):
        if (
            record.action_key != action.action_key
            or record.tool_name is not action.tool_name
        ):
            return _invalid(len(records))

    failed_indexes = [
        index
        for index, record in enumerate(records)
        if record.outcome is AgentExecutionOutcome.FAILED
    ]
    if len(failed_indexes) > 1:
        return _invalid(len(records))
    if failed_indexes:
        failure_index = failed_indexes[0]
        if failure_index != len(records) - 1:
            return _invalid(len(records))
        terminal_status = (
            AgentTerminalStatus.PARTIAL_FAILURE
            if failure_index > 0
            else AgentTerminalStatus.FAILED
        )
        return {
            "verification": AgentVerification(
                status=AgentVerificationStatus.FAILED,
                checked_action_count=len(records),
                error_code=AGENT_EXECUTION_FAILED,
            ),
            "terminal_status": terminal_status,
        }
    if len(records) != len(actions):
        return _invalid(len(records))
    if not actions:
        return {
            "verification": AgentVerification(
                status=AgentVerificationStatus.NO_ACTION,
                checked_action_count=0,
            ),
            "terminal_status": AgentTerminalStatus.SUCCEEDED,
        }
    return {
        "verification": AgentVerification(
            status=AgentVerificationStatus.VERIFIED,
            checked_action_count=len(records),
        ),
        "terminal_status": AgentTerminalStatus.SUCCEEDED,
    }


def _fail_unverified() -> Never:
    raise AgentFinalizationError(AGENT_FINALIZATION_REQUIRED_MESSAGE)


def _summary(state: AgentGraphState) -> str:
    verification = state.verification
    terminal_status = state.terminal_status
    if verification is None or terminal_status is None:
        _fail_unverified()
    if verification.error_code == AGENT_RESULT_INVALID:
        return _SUMMARY_INVALID
    if terminal_status is AgentTerminalStatus.REJECTED:
        return _SUMMARY_REJECTED
    if terminal_status is AgentTerminalStatus.SUCCEEDED:
        if verification.status is AgentVerificationStatus.NO_ACTION:
            return _SUMMARY_NO_ACTION
        return _SUMMARY_SUCCEEDED.format(count=verification.checked_action_count)
    if terminal_status is AgentTerminalStatus.PARTIAL_FAILURE:
        proposal = state.proposal
        assert proposal is not None
        return _SUMMARY_PARTIAL.format(
            succeeded=verification.checked_action_count - 1,
            total=len(proposal.actions),
        )
    return _SUMMARY_FAILED


def summarize(state: AgentGraphState) -> AgentGraphOutput:
    """Create one strict public result only from matching verified state."""

    expected = verify_result(state)
    if (
        state.verification != expected["verification"]
        or state.terminal_status is not expected["terminal_status"]
    ):
        _fail_unverified()
    verification = state.verification
    assert verification is not None
    include_records = verification.error_code not in {
        AGENT_RESULT_INVALID,
        AGENT_WORKFLOW_FAILED,
    }
    proposal = state.proposal
    return AgentGraphOutput(
        status=expected["terminal_status"],
        plan=(
            proposal.planning_result
            if proposal is not None and include_records
            else None
        ),
        summary=_summary(state),
        execution_records=state.execution_records if include_records else (),
        metrics=state.metrics,
    )
