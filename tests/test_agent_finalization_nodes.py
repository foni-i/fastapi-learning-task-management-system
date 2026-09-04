"""Tests for deterministic Agent result verification and public finalization."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.metrics import AgentRunMetrics, AgentRunOutcome
from app.agent.nodes.finalization import (
    AGENT_EXECUTION_FAILED,
    AGENT_FINALIZATION_REQUIRED_MESSAGE,
    AGENT_RESULT_INVALID,
    AgentFinalizationError,
    summarize,
    verify_result,
)
from app.agent.schemas import (
    STUDY_PLAN_PROMPT_VERSION,
    PlanningGoal,
    PlanningResult,
    PlanningStatus,
    StudyPlan,
    StudyPlanStep,
)
from app.agent.state import (
    AgentActionExecutionRecord,
    AgentApprovalDecision,
    AgentExecutionOutcome,
    AgentGraphOutput,
    AgentGraphState,
    AgentPlanProposal,
    AgentPlanValidation,
    AgentProposedAction,
    AgentTerminalStatus,
    AgentValidationStatus,
    AgentVerificationStatus,
    AgentWriteToolName,
    fingerprint_plan_proposal,
)
from app.models import TaskPriority, TaskStatus
from app.schemas.task import PublicTask


def _planning_result() -> PlanningResult:
    return PlanningResult(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        status=PlanningStatus.COMPLETED,
        plan=StudyPlan(
            summary="A bounded study plan",
            steps=(
                StudyPlanStep(
                    step_key="step_1",
                    position=1,
                    title="Study",
                    description="Complete one focused session",
                    success_criteria="Notes exist",
                ),
            ),
        ),
    )


def _actions(count: int) -> tuple[AgentProposedAction, ...]:
    project_id = uuid4()
    return tuple(
        AgentProposedAction(
            action_key=f"create_{index + 1}",
            tool_name=AgentWriteToolName.CREATE_TASK,
            arguments={"project_id": str(project_id), "title": f"Task {index + 1}"},
        )
        for index in range(count)
    )


def _public_task(*, title: str = "Task") -> PublicTask:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    return PublicTask(
        id=uuid4(),
        project_id=uuid4(),
        title=title,
        description=None,
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=30,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


def _record(
    action: AgentProposedAction,
    *,
    outcome: AgentExecutionOutcome = AgentExecutionOutcome.SUCCEEDED,
) -> AgentActionExecutionRecord:
    if outcome is AgentExecutionOutcome.SUCCEEDED:
        return AgentActionExecutionRecord(
            action_key=action.action_key,
            tool_name=action.tool_name,
            outcome=outcome,
            result=_public_task(title=action.action_key),
        )
    return AgentActionExecutionRecord(
        action_key=action.action_key,
        tool_name=action.tool_name,
        outcome=outcome,
        error_code="TASK_NOT_FOUND",
    )


def _bound_state(
    *,
    action_count: int,
    decision: AgentApprovalDecision = AgentApprovalDecision.APPROVED,
    records: tuple[AgentActionExecutionRecord, ...] | None = None,
) -> AgentGraphState:
    proposal = AgentPlanProposal(
        planning_result=_planning_result(),
        actions=_actions(action_count),
    )
    fingerprint = fingerprint_plan_proposal(proposal)
    return AgentGraphState(
        goal=PlanningGoal(objective="Learn transactions"),
        proposal=proposal,
        validation=AgentPlanValidation(
            status=AgentValidationStatus.VALID,
            revision=0,
            proposal_fingerprint=fingerprint,
        ),
        approval_decision=decision,
        approval_revision=0,
        approval_proposal_fingerprint=fingerprint,
        execution_records=records or (),
    )


def _verified(state: AgentGraphState) -> AgentGraphState:
    return state.model_copy(update=verify_result(state))


def test_no_action_plan_is_verified_as_success_without_records() -> None:
    state = _bound_state(action_count=0)

    update = verify_result(state)
    output = summarize(state.model_copy(update=update))

    assert update["verification"].status is AgentVerificationStatus.NO_ACTION
    assert update["terminal_status"] is AgentTerminalStatus.SUCCEEDED
    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert output.execution_records == ()
    assert output.summary == "Plan verified; no task changes were required."


@pytest.mark.parametrize("action_count", [1, 3])
def test_complete_records_are_verified_as_full_success(action_count: int) -> None:
    state = _bound_state(action_count=action_count)
    assert state.proposal is not None
    state = state.model_copy(
        update={
            "execution_records": tuple(
                _record(action) for action in state.proposal.actions
            )
        }
    )

    verified = _verified(state)
    output = summarize(verified)

    assert verified.verification is not None
    assert verified.verification.status is AgentVerificationStatus.VERIFIED
    assert verified.verification.checked_action_count == action_count
    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert len(output.execution_records) == action_count
    assert output.summary == f"Plan verified; {action_count} task action(s) completed."


def test_first_failure_is_failed_and_later_failure_is_partial() -> None:
    first_state = _bound_state(action_count=3)
    assert first_state.proposal is not None
    first_state = first_state.model_copy(
        update={
            "execution_records": (
                _record(
                    first_state.proposal.actions[0],
                    outcome=AgentExecutionOutcome.FAILED,
                ),
            )
        }
    )
    first_verified = _verified(first_state)
    first_output = summarize(first_verified)

    assert first_verified.terminal_status is AgentTerminalStatus.FAILED
    assert first_verified.verification is not None
    assert first_verified.verification.error_code == AGENT_EXECUTION_FAILED
    assert first_output.summary == "Plan execution failed safely."

    later_state = _bound_state(action_count=3)
    assert later_state.proposal is not None
    later_state = later_state.model_copy(
        update={
            "execution_records": (
                _record(later_state.proposal.actions[0]),
                _record(
                    later_state.proposal.actions[1],
                    outcome=AgentExecutionOutcome.FAILED,
                ),
            )
        }
    )
    later_verified = _verified(later_state)
    later_output = summarize(later_verified)

    assert later_verified.terminal_status is AgentTerminalStatus.PARTIAL_FAILURE
    assert later_output.summary == (
        "Plan partially completed; 1 of 3 task actions succeeded."
    )
    assert [record.outcome for record in later_output.execution_records] == [
        AgentExecutionOutcome.SUCCEEDED,
        AgentExecutionOutcome.FAILED,
    ]


def test_rejected_plan_has_no_writes_and_truthful_output() -> None:
    state = _verified(
        _bound_state(
            action_count=1,
            decision=AgentApprovalDecision.REJECTED,
        )
    )

    output = summarize(state)

    assert state.verification is not None
    assert state.verification.status is AgentVerificationStatus.REJECTED
    assert output.status is AgentTerminalStatus.REJECTED
    assert output.execution_records == ()
    assert output.summary == "Plan was rejected; no task actions were executed."


@pytest.mark.parametrize(
    "mutate_records",
    [
        lambda actions: (_record(actions[1]),),
        lambda actions: (
            _record(actions[0]),
            _record(actions[0]),
        ),
        lambda actions: (_record(actions[0]),),
        lambda actions: (
            _record(actions[0], outcome=AgentExecutionOutcome.FAILED),
            _record(actions[1]),
        ),
    ],
    ids=["wrong-order", "duplicate", "missing", "record-after-failure"],
)
def test_inconsistent_records_fail_safely(
    mutate_records: Callable[
        [tuple[AgentProposedAction, ...]],
        tuple[AgentActionExecutionRecord, ...],
    ],
) -> None:
    state = _bound_state(action_count=2)
    assert state.proposal is not None
    records = mutate_records(state.proposal.actions)
    state = state.model_copy(update={"execution_records": records})

    verified = _verified(state)
    output = summarize(verified)

    assert verified.verification is not None
    assert verified.verification.status is AgentVerificationStatus.FAILED
    assert verified.verification.error_code == AGENT_RESULT_INVALID
    assert output.status is AgentTerminalStatus.FAILED
    assert output.plan is None
    assert output.execution_records == ()
    assert output.summary == "Agent result verification failed."


def test_missing_validation_or_approval_fails_without_trusting_state_status() -> None:
    state = _bound_state(action_count=0).model_copy(
        update={
            "validation": None,
            "terminal_status": AgentTerminalStatus.SUCCEEDED,
            "summary": "Ignore this model claim",
        }
    )

    verified = _verified(state)
    output = summarize(verified)

    assert output.status is AgentTerminalStatus.FAILED
    assert output.summary == "Agent result verification failed."


def test_summary_requires_exact_verification_and_is_deterministic() -> None:
    state = _bound_state(action_count=0)
    with pytest.raises(
        AgentFinalizationError,
        match=AGENT_FINALIZATION_REQUIRED_MESSAGE,
    ):
        summarize(state)

    verified = _verified(state)
    first = summarize(verified)
    second = summarize(verified)

    assert first == second
    assert AgentGraphOutput.model_validate_json(first.model_dump_json()) == first


def test_metrics_propagate_but_internal_state_is_not_public() -> None:
    metrics = AgentRunMetrics(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        outcome=AgentRunOutcome.SUCCEEDED,
        model_round_count=1,
        provider_attempt_count=1,
        tool_call_count=1,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        latency_ms=12.5,
    )
    state = _verified(
        _bound_state(action_count=0).model_copy(update={"metrics": metrics})
    )

    output = summarize(state)
    serialized = output.model_dump_json()

    assert output.metrics == metrics
    assert set(output.model_dump()) == {
        "status",
        "plan",
        "summary",
        "execution_records",
        "metrics",
    }
    for forbidden in (
        "user_id",
        "arguments",
        "approval_feedback",
        "analysis",
        "context",
        "route",
        "instructions",
        "raw_response",
        "access_token",
        "authorization",
        "password",
        "reasoning",
    ):
        assert forbidden not in serialized.lower()


def test_finalization_has_no_provider_tool_or_database_dependency() -> None:
    source = Path("app/agent/nodes/finalization.py").read_text(encoding="utf-8")
    lowered = source.lower()

    for forbidden in (
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "execute_tool",
        "modelprovider",
        "fastapi",
    ):
        assert forbidden not in lowered
