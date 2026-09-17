"""Tests for explicit bounded Agent approval and pure routing."""

from collections.abc import Iterable
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import (
    AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE,
    AGENT_APPROVAL_REQUIRED_MESSAGE,
    AGENT_APPROVAL_UNAVAILABLE_MESSAGE,
    MAX_APPROVAL_PREVIEW_BYTES,
    PLAN_REVISION_LIMIT_MESSAGE,
    AgentApprovalInterrupt,
    AgentApprovalProposalPreview,
    AgentApprovalRequest,
    AgentApprovalRequiredError,
    AgentApprovalResponse,
    AgentApprovalUnavailableError,
    CreateTaskApprovalPreview,
    request_approval,
)
from app.agent.nodes.planning import validate_plan
from app.agent.routing import (
    AGENT_APPROVAL_DECISION_MISSING_MESSAGE,
    AgentRoute,
    AgentRoutingError,
    route_after_approval,
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
    AgentApprovalDecision,
    AgentContextKind,
    AgentContextSnapshot,
    AgentGoalAnalysis,
    AgentGraphState,
    AgentPlanProposal,
    AgentProposedAction,
    AgentTerminalStatus,
    AgentWriteToolName,
    fingerprint_plan_proposal,
)
from app.schemas.project import ProjectListResponse
from app.schemas.task import TaskListResponse


class SequenceDecider:
    def __init__(self, decisions: Iterable[AgentApprovalResponse | Exception]) -> None:
        self.decisions = list(decisions)
        self.requests: list[AgentApprovalInterrupt] = []

    def decide(self, request: AgentApprovalRequest) -> AgentApprovalResponse:
        self.requests.append(cast(AgentApprovalInterrupt, request))
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True)


def _proposal(*, title: str = "Read") -> AgentPlanProposal:
    return AgentPlanProposal(
        planning_result=PlanningResult(
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
        ),
        actions=(
            AgentProposedAction(
                action_key="create_1",
                tool_name=AgentWriteToolName.CREATE_TASK,
                arguments={"project_id": str(uuid4()), "title": title},
            ),
        ),
    )


def _validated_state(*, revision: int = 0) -> AgentGraphState:
    state = AgentGraphState(
        goal=PlanningGoal(objective="Learn transactions"),
        analysis=AgentGoalAnalysis(
            objective="Learn transactions",
            required_context=(AgentContextKind.PROJECTS, AgentContextKind.TASKS),
        ),
        context=AgentContextSnapshot(
            projects=ProjectListResponse(
                items=[], page=1, page_size=20, total=0, pages=0
            ),
            tasks=TaskListResponse(items=[], page=1, page_size=20, total=0, pages=0),
        ),
        proposal=_proposal(),
        revision_count=revision,
    )
    return state.model_copy(
        update=validate_plan(
            state,
            runtime_context=AgentRuntimeContext(
                user_id=uuid4(), write_tools_enabled=True
            ),
        )
    )


@pytest.mark.parametrize(
    ("decision", "route"),
    [
        (AgentApprovalDecision.APPROVED, AgentRoute.EXECUTE_TASKS),
        (AgentApprovalDecision.REJECTED, AgentRoute.SUMMARIZE),
    ],
)
def test_approve_and_reject_bind_exact_safe_plan(
    decision: AgentApprovalDecision,
    route: AgentRoute,
) -> None:
    state = _validated_state()
    decider = SequenceDecider([AgentApprovalResponse(decision=decision)])

    update = request_approval(
        state,
        decider=decider,
        runtime_context=_runtime_context(),
    )
    decided = state.model_copy(update=update)

    assert update["approval_decision"] is decision
    assert update["approval_revision"] == 0
    assert update["approval_proposal_fingerprint"] == (
        state.validation.proposal_fingerprint if state.validation else None
    )
    assert route_after_approval(decided) is route
    request = decider.requests[0]
    assert request.plan_summary == "A bounded study plan"
    assert request.action_names == (AgentWriteToolName.CREATE_TASK,)
    assert request.action_count == 1
    serialized = request.model_dump_json()
    assert "project_id" in serialized
    assert '"title":"Read"' in serialized
    assert "user_id" not in serialized
    assert "arguments" not in serialized
    assert request.preview.to_plan_proposal() == state.proposal
    assert request.preview.proposal_fingerprint == fingerprint_plan_proposal(
        request.preview.to_plan_proposal()
    )
    if decision is AgentApprovalDecision.REJECTED:
        assert update["terminal_status"] is AgentTerminalStatus.REJECTED


def test_two_change_requests_force_new_proposal_and_validation() -> None:
    state = _validated_state()

    for expected_revision in (1, 2):
        update = request_approval(
            state,
            decider=SequenceDecider(
                [
                    AgentApprovalResponse(
                        decision=AgentApprovalDecision.REQUEST_CHANGES,
                        feedback="  Make it smaller  ",
                    )
                ]
            ),
            runtime_context=_runtime_context(),
        )
        state = state.model_copy(update=update)
        assert state.revision_count == expected_revision
        assert state.approval_feedback == "Make it smaller"
        assert state.proposal is None
        assert state.validation is None
        assert state.approval_revision is None
        assert state.approval_proposal_fingerprint is None
        with pytest.raises(AgentApprovalRequiredError):
            request_approval(
                state,
                decider=SequenceDecider(
                    [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
                ),
                runtime_context=_runtime_context(),
            )
        state = _validated_state(revision=expected_revision)


def test_third_change_request_fails_closed_without_increment() -> None:
    state = _validated_state(revision=2)

    update = request_approval(
        state,
        decider=SequenceDecider(
            [
                AgentApprovalResponse(
                    decision=AgentApprovalDecision.REQUEST_CHANGES,
                    feedback="Try again",
                )
            ]
        ),
        runtime_context=_runtime_context(),
    )

    assert update["approval_decision"] is AgentApprovalDecision.REJECTED
    assert update["terminal_status"] is AgentTerminalStatus.REJECTED
    assert update["summary"] == PLAN_REVISION_LIMIT_MESSAGE
    assert "revision_count" not in update
    assert "proposal" not in update


def test_unvalidated_stale_or_tampered_plan_never_calls_decider() -> None:
    valid = _validated_state()
    states = [
        AgentGraphState(goal=valid.goal, proposal=valid.proposal),
        valid.model_copy(update={"revision_count": 1}),
        valid.model_copy(update={"proposal": _proposal(title="Changed")}),
    ]

    for state in states:
        decider = SequenceDecider(
            [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
        )
        with pytest.raises(
            AgentApprovalRequiredError,
            match=AGENT_APPROVAL_REQUIRED_MESSAGE,
        ):
            request_approval(
                state,
                decider=decider,
                runtime_context=_runtime_context(),
            )
        assert decider.requests == []


def test_decision_contract_rejects_invalid_feedback_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AgentApprovalResponse(decision=AgentApprovalDecision.REQUEST_CHANGES)
    with pytest.raises(ValidationError):
        AgentApprovalResponse(
            decision=AgentApprovalDecision.APPROVED,
            feedback="not allowed",
        )
    with pytest.raises(ValidationError):
        AgentApprovalResponse.model_validate(
            {"decision": "approved", "grant_write_access": True}
        )
    with pytest.raises(ValidationError):
        AgentApprovalResponse.model_validate(
            {"decision": "unknown", "feedback": "private"}
        )


def test_model_proposal_cannot_approve_itself_and_input_state_is_unchanged() -> None:
    proposal = _proposal()
    with pytest.raises(ValidationError):
        AgentPlanProposal.model_validate(
            {
                **proposal.model_dump(mode="python"),
                "approval_decision": "approved",
            }
        )

    state = _validated_state()
    original = state.model_dump_json()
    request_approval(
        state,
        decider=SequenceDecider(
            [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
        ),
        runtime_context=_runtime_context(),
    )
    assert state.model_dump_json() == original


def test_adapter_failure_is_replaced_with_fixed_safe_error() -> None:
    private_detail = "private approval adapter response"

    with pytest.raises(
        AgentApprovalUnavailableError,
        match=AGENT_APPROVAL_UNAVAILABLE_MESSAGE,
    ) as exc_info:
        request_approval(
            _validated_state(),
            decider=SequenceDecider([RuntimeError(private_detail)]),
            runtime_context=_runtime_context(),
        )

    assert private_detail not in str(exc_info.value)


def test_routing_is_pure_allowlisted_and_requires_a_decision() -> None:
    goal = PlanningGoal(objective="Learn")
    for decision, expected in (
        (AgentApprovalDecision.APPROVED, AgentRoute.EXECUTE_TASKS),
        (AgentApprovalDecision.REJECTED, AgentRoute.SUMMARIZE),
        (AgentApprovalDecision.REQUEST_CHANGES, AgentRoute.GENERATE_PLAN),
    ):
        assert (
            route_after_approval(AgentGraphState(goal=goal, approval_decision=decision))
            is expected
        )

    with pytest.raises(
        AgentRoutingError,
        match=AGENT_APPROVAL_DECISION_MISSING_MESSAGE,
    ):
        route_after_approval(AgentGraphState(goal=goal))


def test_approval_state_round_trip_contains_no_arguments_or_identity() -> None:
    state = _validated_state()
    update = request_approval(
        state,
        decider=SequenceDecider(
            [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
        ),
        runtime_context=_runtime_context(),
    )
    decided = state.model_copy(update=update)
    round_tripped = AgentGraphState.model_validate_json(decided.model_dump_json())
    serialized_update = str(update)

    assert round_tripped == decided
    assert "project_id" not in serialized_update
    assert "user_id" not in serialized_update
    assert "api_key" not in serialized_update
    assert "reasoning" not in serialized_update


def test_preview_rejects_tampering_and_enforces_utf8_byte_cap() -> None:
    state = _validated_state()
    decider = SequenceDecider(
        [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
    )
    request_approval(
        state,
        decider=decider,
        runtime_context=_runtime_context(),
    )
    preview = decider.requests[0].preview
    assert len(preview.canonical_json().encode("utf-8")) <= (MAX_APPROVAL_PREVIEW_BYTES)

    action = preview.actions[0]
    assert isinstance(action, CreateTaskApprovalPreview)
    with pytest.raises(ValidationError, match=AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE):
        AgentApprovalProposalPreview(
            revision=preview.revision,
            proposal_fingerprint=preview.proposal_fingerprint,
            planning_result=preview.planning_result,
            actions=(
                action.model_copy(
                    update={"task": action.task.model_copy(update={"title": "Changed"})}
                ),
            ),
        )


def test_oversize_preview_fails_closed_before_decider_without_truncation() -> None:
    project_id = uuid4()
    proposal = AgentPlanProposal(
        planning_result=PlanningResult(
            prompt_version=STUDY_PLAN_PROMPT_VERSION,
            status=PlanningStatus.COMPLETED,
            plan=StudyPlan(
                summary="s" * 1_000,
                steps=tuple(
                    StudyPlanStep(
                        step_key=f"step_{position}",
                        position=position,
                        title="t" * 200,
                        description="d" * 1_000,
                        success_criteria="c" * 500,
                    )
                    for position in range(1, 21)
                ),
            ),
        ),
        actions=(
            AgentProposedAction(
                action_key="batch_1",
                tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
                arguments={
                    "tasks": [
                        {
                            "project_id": str(project_id),
                            "title": f"Task {index}",
                            "description": "x" * 5_000,
                        }
                        for index in range(10)
                    ]
                },
            ),
        ),
    )
    state = _validated_state().model_copy(
        update={"proposal": proposal, "validation": None}
    )
    context = _runtime_context()
    state = state.model_copy(update=validate_plan(state, runtime_context=context))
    decider = SequenceDecider(
        [AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)]
    )

    with pytest.raises(
        AgentApprovalRequiredError,
        match=AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE,
    ):
        request_approval(
            state,
            decider=decider,
            runtime_context=context,
        )

    assert decider.requests == []


def test_approval_node_has_no_execution_or_persistence_dependency() -> None:
    from pathlib import Path

    source = Path("app/agent/nodes/approval.py").read_text(encoding="utf-8")

    for forbidden in (
        "execute_tool",
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "fastapi",
    ):
        assert forbidden not in source.lower()
