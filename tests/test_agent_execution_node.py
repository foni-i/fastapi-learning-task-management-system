"""Tests for bounded execution of explicitly approved Agent actions."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import AgentApprovalResponse, request_approval
from app.agent.nodes.execution import (
    AGENT_ACTION_FAILED,
    AGENT_EXECUTION_NOT_AUTHORIZED_MESSAGE,
    AgentExecutionNotAuthorizedError,
    execute_tasks,
)
from app.agent.nodes.planning import validate_plan
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
    AgentExecutionOutcome,
    AgentGoalAnalysis,
    AgentGraphState,
    AgentPlanProposal,
    AgentProposedAction,
    AgentWriteToolName,
)
from app.agent.tools import AgentToolGateway, AgentToolResult
from app.core.exceptions import ProjectNotFoundError, TaskNotFoundError
from app.models import TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import PublicTask, TaskListResponse


class Approver:
    def decide(self, request: object) -> AgentApprovalResponse:
        return AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)


class RecordingDispatcher:
    def __init__(
        self,
        outcomes: list[PublicTask | Exception],
    ) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, Mapping[str, object], AgentRuntimeContext]] = []

    def __call__(
        self,
        name: str,
        arguments: Mapping[str, object],
        context: AgentRuntimeContext,
        *,
        gateway: AgentToolGateway | None = None,
    ) -> AgentToolResult:
        self.calls.append((name, arguments, context))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _task(*, title: str = "Read") -> PublicTask:
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


def _actions(count: int) -> tuple[AgentProposedAction, ...]:
    project_id = uuid4()
    actions: list[AgentProposedAction] = []
    for index in range(count):
        if index == 1:
            actions.append(
                AgentProposedAction(
                    action_key="update_1",
                    tool_name=AgentWriteToolName.UPDATE_TASK,
                    arguments={"task_id": str(uuid4()), "title": "Review"},
                )
            )
        else:
            actions.append(
                AgentProposedAction(
                    action_key=f"create_{index + 1}",
                    tool_name=AgentWriteToolName.CREATE_TASK,
                    arguments={"project_id": str(project_id), "title": "Read"},
                )
            )
    return tuple(actions)


def _approved_state(*, action_count: int = 1) -> tuple[AgentGraphState, UUID]:
    user_id = uuid4()
    proposal = AgentPlanProposal(
        planning_result=PlanningResult(
            prompt_version=STUDY_PLAN_PROMPT_VERSION,
            status=PlanningStatus.COMPLETED,
            plan=StudyPlan(
                summary="A bounded plan",
                steps=(
                    StudyPlanStep(
                        step_key="step_1",
                        position=1,
                        title="Study",
                        description="Complete one session",
                        success_criteria="Notes exist",
                    ),
                ),
            ),
        ),
        actions=_actions(action_count),
    )
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
        proposal=proposal,
    )
    runtime_context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    state = state.model_copy(
        update=validate_plan(state, runtime_context=runtime_context)
    )
    state = state.model_copy(update=request_approval(state, decider=Approver()))
    return state, user_id


@pytest.mark.parametrize("action_count", [1, 3])
def test_executes_one_or_three_actions_once_in_order(action_count: int) -> None:
    state, user_id = _approved_state(action_count=action_count)
    dispatcher = RecordingDispatcher(
        [_task(title=f"Result {index}") for index in range(action_count)]
    )
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    original = state.model_dump_json()

    update = execute_tasks(
        state,
        runtime_context=context,
        dispatcher=dispatcher,
    )

    assert len(update["execution_records"]) == action_count
    assert state.proposal is not None
    assert [call[0] for call in dispatcher.calls] == [
        action.tool_name.value for action in state.proposal.actions
    ]
    assert all(call[2].user_id == user_id for call in dispatcher.calls)
    assert all("user_id" not in call[1] for call in dispatcher.calls)
    assert all(
        record.outcome is AgentExecutionOutcome.SUCCEEDED
        for record in update["execution_records"]
    )
    assert state.model_dump_json() == original


def test_zero_action_plan_dispatches_nothing() -> None:
    state, user_id = _approved_state(action_count=0)
    dispatcher = RecordingDispatcher([])

    update = execute_tasks(
        state,
        runtime_context=AgentRuntimeContext(user_id=user_id, write_tools_enabled=True),
        dispatcher=dispatcher,
    )

    assert update == {"execution_records": ()}
    assert dispatcher.calls == []


def test_validation_approval_and_runtime_write_capability_are_all_required() -> None:
    approved, user_id = _approved_state()
    states = [
        approved.model_copy(update={"validation": None}),
        approved.model_copy(update={"approval_decision": None}),
        approved.model_copy(
            update={"approval_decision": AgentApprovalDecision.REJECTED}
        ),
        approved.model_copy(
            update={"approval_decision": AgentApprovalDecision.REQUEST_CHANGES}
        ),
        approved.model_copy(update={"approval_revision": 1}),
        approved.model_copy(update={"approval_proposal_fingerprint": "0" * 64}),
    ]

    for state in states:
        dispatcher = RecordingDispatcher([_task()])
        with pytest.raises(
            AgentExecutionNotAuthorizedError,
            match=AGENT_EXECUTION_NOT_AUTHORIZED_MESSAGE,
        ):
            execute_tasks(
                state,
                runtime_context=AgentRuntimeContext(
                    user_id=user_id, write_tools_enabled=True
                ),
                dispatcher=dispatcher,
            )
        assert dispatcher.calls == []

    with pytest.raises(AgentExecutionNotAuthorizedError):
        execute_tasks(
            approved,
            runtime_context=AgentRuntimeContext(user_id=user_id),
            dispatcher=RecordingDispatcher([_task()]),
        )


def test_replaced_proposal_cannot_reuse_old_validation_and_approval() -> None:
    state, user_id = _approved_state()
    assert state.proposal is not None
    changed = state.proposal.model_copy(update={"actions": _actions(1)})
    tampered = state.model_copy(update={"proposal": changed})
    dispatcher = RecordingDispatcher([_task()])

    with pytest.raises(AgentExecutionNotAuthorizedError):
        execute_tasks(
            tampered,
            runtime_context=AgentRuntimeContext(
                user_id=user_id, write_tools_enabled=True
            ),
            dispatcher=dispatcher,
        )

    assert dispatcher.calls == []


def test_every_action_is_revalidated_immediately_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.tools import validate_tool_arguments as original_validate

    state, user_id = _approved_state(action_count=3)
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    dispatcher = RecordingDispatcher([_task(), _task(), _task()])
    validations: list[str] = []

    def record_validation(
        name: str,
        arguments: Mapping[str, object],
        runtime_context: AgentRuntimeContext,
    ) -> object:
        validations.append(name)
        return original_validate(name, arguments, runtime_context)

    monkeypatch.setattr(
        "app.agent.nodes.execution.validate_tool_arguments",
        record_validation,
    )

    execute_tasks(state, runtime_context=context, dispatcher=dispatcher)

    assert state.proposal is not None
    action_names = [action.tool_name.value for action in state.proposal.actions]
    assert validations == action_names + action_names
    assert [call[0] for call in dispatcher.calls] == action_names


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (TaskNotFoundError("private"), "TASK_NOT_FOUND"),
        (ProjectNotFoundError("private"), "PROJECT_NOT_FOUND"),
        (RuntimeError("private database diagnostic"), AGENT_ACTION_FAILED),
    ],
    ids=["task-missing", "project-missing", "unexpected"],
)
def test_first_failure_is_safe_not_retried_and_stops_batch(
    failure: Exception,
    error_code: str,
) -> None:
    state, user_id = _approved_state(action_count=3)
    dispatcher = RecordingDispatcher([failure, _task(), _task()])

    update = execute_tasks(
        state,
        runtime_context=AgentRuntimeContext(user_id=user_id, write_tools_enabled=True),
        dispatcher=dispatcher,
    )

    assert len(dispatcher.calls) == 1
    assert len(update["execution_records"]) == 1
    record = update["execution_records"][0]
    assert record.outcome is AgentExecutionOutcome.FAILED
    assert record.error_code == error_code
    assert record.result is None
    assert "private" not in record.model_dump_json()


def test_later_failure_preserves_truthful_partial_success() -> None:
    state, user_id = _approved_state(action_count=3)
    dispatcher = RecordingDispatcher([_task(), TaskNotFoundError("private"), _task()])

    update = execute_tasks(
        state,
        runtime_context=AgentRuntimeContext(user_id=user_id, write_tools_enabled=True),
        dispatcher=dispatcher,
    )

    records = update["execution_records"]
    assert len(dispatcher.calls) == 2
    assert [record.outcome for record in records] == [
        AgentExecutionOutcome.SUCCEEDED,
        AgentExecutionOutcome.FAILED,
    ]
    assert records[0].result is not None
    assert records[1].error_code == "TASK_NOT_FOUND"


def test_execution_records_expose_only_public_task_fields() -> None:
    state, user_id = _approved_state()
    update = execute_tasks(
        state,
        runtime_context=AgentRuntimeContext(user_id=user_id, write_tools_enabled=True),
        dispatcher=RecordingDispatcher([_task()]),
    )
    record = update["execution_records"][0]
    serialized = record.model_dump_json()

    assert record.result is not None
    assert set(record.result.model_dump()) == {
        "id",
        "project_id",
        "title",
        "description",
        "status",
        "priority",
        "planned_date",
        "due_at",
        "estimated_minutes",
        "completed_at",
        "created_at",
        "updated_at",
    }
    assert "user_id" not in serialized
    assert "arguments" not in serialized
    assert "sql" not in serialized.lower()
    assert "reasoning" not in serialized


def test_execution_node_has_no_persistence_service_or_http_dependency() -> None:
    source = Path("app/agent/nodes/execution.py").read_text(encoding="utf-8")
    lowered = source.lower()

    for forbidden in (
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "app.services",
        "agentdomaingateway",
        "fastapi",
    ):
        assert forbidden not in lowered
