"""Strict state-boundary tests for the Stage 9 Agent workflow."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.metrics import AgentRunMetrics, AgentRunOutcome
from app.agent.schemas import (
    STUDY_PLAN_PROMPT_VERSION,
    PlanningGoal,
    PlanningResult,
    PlanningStatus,
    StudyPlan,
    StudyPlanStep,
)
from app.agent.state import (
    MAX_PLAN_ACTIONS,
    AgentActionExecutionRecord,
    AgentContextKind,
    AgentContextSnapshot,
    AgentExecutionOutcome,
    AgentGoalAnalysis,
    AgentGraphInput,
    AgentGraphOutput,
    AgentGraphState,
    AgentPlanProposal,
    AgentProposedAction,
    AgentTerminalStatus,
    AgentWriteToolName,
)
from app.models import ProjectStatus, TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import PublicTask, TaskListResponse


def _planning_result() -> PlanningResult:
    return PlanningResult(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        status=PlanningStatus.COMPLETED,
        plan=StudyPlan(
            summary="A bounded plan",
            steps=(
                StudyPlanStep(
                    step_key="step_1",
                    position=1,
                    title="Read",
                    description="Read one chapter",
                    success_criteria="Notes exist",
                ),
            ),
        ),
    )


def _public_task() -> PublicTask:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    return PublicTask(
        id=uuid4(),
        project_id=uuid4(),
        title="Read",
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


def _context() -> AgentContextSnapshot:
    return AgentContextSnapshot(
        projects=ProjectListResponse(items=[], page=1, page_size=20, total=0, pages=0),
        tasks=TaskListResponse(items=[], page=1, page_size=20, total=0, pages=0),
    )


def test_minimal_input_and_state_are_strict_frozen_and_json_safe() -> None:
    goal = PlanningGoal(objective="Build a study routine")
    graph_input = AgentGraphInput(goal=goal)
    state = AgentGraphState(goal=goal)

    assert set(AgentGraphInput.model_fields) == {"goal"}
    assert (
        AgentGraphInput.model_validate_json(graph_input.model_dump_json())
        == graph_input
    )
    assert AgentGraphState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        AgentGraphInput.model_validate({"goal": goal, "user_id": str(uuid4())})
    with pytest.raises(ValidationError):
        AgentGraphState.model_validate({"goal": goal, "session": "forbidden"})
    with pytest.raises(ValidationError):
        state.revision_count = 1


def test_analysis_and_context_are_bounded_public_values() -> None:
    analysis = AgentGoalAnalysis(
        objective="  Learn transactions  ",
        constraints=("  Finish this week  ",),
        required_context=(AgentContextKind.PROJECTS, AgentContextKind.TASKS),
    )
    state = AgentGraphState(
        goal=PlanningGoal(objective="Learn transactions"),
        analysis=analysis,
        context=_context(),
    )

    assert analysis.objective == "Learn transactions"
    assert analysis.constraints == ("Finish this week",)
    assert state.context is not None
    assert set(state.context.model_dump()) == {"projects", "tasks"}
    with pytest.raises(ValidationError, match="must be unique"):
        AgentGoalAnalysis(
            objective="Learn",
            required_context=(AgentContextKind.PROJECTS, AgentContextKind.PROJECTS),
        )


def test_proposal_allows_only_unique_bounded_task_write_actions() -> None:
    project_id = uuid4()
    action = AgentProposedAction(
        action_key="create_1",
        tool_name=AgentWriteToolName.CREATE_TASK,
        arguments={"project_id": str(project_id), "title": "Read"},
    )
    proposal = AgentPlanProposal(planning_result=_planning_result(), actions=(action,))

    assert proposal.actions == (action,)
    assert AgentPlanProposal.model_validate_json(proposal.model_dump_json()) == proposal
    with pytest.raises(ValidationError):
        AgentProposedAction.model_validate(
            {"action_key": "read_1", "tool_name": "list_tasks", "arguments": {}}
        )
    with pytest.raises(ValidationError, match="forbidden field"):
        AgentProposedAction(
            action_key="create_1",
            tool_name=AgentWriteToolName.CREATE_TASK,
            arguments={"user_id": str(uuid4())},
        )
    with pytest.raises(ValidationError, match="keys must be unique"):
        AgentPlanProposal(planning_result=_planning_result(), actions=(action, action))

    actions = tuple(
        AgentProposedAction(
            action_key=f"create_{index}",
            tool_name=AgentWriteToolName.CREATE_TASK,
            arguments={"project_id": str(project_id), "title": f"Task {index}"},
        )
        for index in range(MAX_PLAN_ACTIONS + 1)
    )
    with pytest.raises(ValidationError):
        AgentPlanProposal(planning_result=_planning_result(), actions=actions)


def test_revision_text_and_collection_bounds_are_enforced() -> None:
    goal = PlanningGoal(objective="Learn")
    with pytest.raises(ValidationError):
        AgentGraphState(goal=goal, revision_count=3)
    with pytest.raises(ValidationError):
        AgentGraphState(goal=goal, approval_feedback="x" * 1_001)
    with pytest.raises(ValidationError):
        AgentGraphState(goal=goal, summary="x" * 2_001)
    with pytest.raises(ValidationError):
        AgentGoalAnalysis(
            objective="Learn",
            constraints=tuple(f"rule-{index}" for index in range(21)),
            required_context=(AgentContextKind.PROJECTS,),
        )


def test_public_output_is_an_explicit_safe_whitelist() -> None:
    record = AgentActionExecutionRecord(
        action_key="create_1",
        tool_name=AgentWriteToolName.CREATE_TASK,
        outcome=AgentExecutionOutcome.SUCCEEDED,
        result=_public_task(),
    )
    metrics = AgentRunMetrics(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        outcome=AgentRunOutcome.SUCCEEDED,
        model_round_count=1,
        provider_attempt_count=1,
        tool_call_count=1,
        latency_ms=1.5,
    )
    output = AgentGraphOutput(
        status=AgentTerminalStatus.SUCCEEDED,
        plan=_planning_result(),
        summary="Plan accepted",
        execution_records=(record,),
        metrics=metrics,
    )
    dumped = output.model_dump(mode="json")
    serialized = output.model_dump_json()

    assert set(dumped) == {
        "status",
        "plan",
        "summary",
        "execution_records",
        "metrics",
    }
    assert "user_id" not in serialized
    assert "password" not in serialized
    assert "api_key" not in serialized
    assert "reasoning" not in serialized
    assert AgentGraphOutput.model_validate_json(serialized) == output
    with pytest.raises(ValidationError):
        AgentGraphOutput.model_validate({**dumped, "internal_state": {}})


def test_state_contract_has_no_runtime_identity_or_dependency_fields() -> None:
    forbidden = {
        "user_id",
        "session",
        "connection",
        "repository",
        "service",
        "provider",
        "api_key",
        "access_token",
        "prompt",
        "raw_response",
        "reasoning",
    }

    assert forbidden.isdisjoint(AgentGraphInput.model_fields)
    assert forbidden.isdisjoint(AgentGraphState.model_fields)
    assert forbidden.isdisjoint(AgentGraphOutput.model_fields)
    assert "user_id" not in AgentPlanProposal.model_json_schema()["properties"]


def test_existing_public_context_rejects_internal_owner_fields() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ProjectListResponse.model_validate(
            {
                "items": [
                    {
                        "id": str(uuid4()),
                        "user_id": str(uuid4()),
                        "name": "Private",
                        "description": None,
                        "start_date": None,
                        "target_date": None,
                        "status": ProjectStatus.NOT_STARTED,
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
                "page": 1,
                "page_size": 20,
                "total": 1,
                "pages": 1,
            }
        )
