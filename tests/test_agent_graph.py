"""Offline integration tests for the bounded eight-node LangGraph workflow."""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.graph import (
    AGENT_GRAPH_FAILURE_SUMMARY,
    AGENT_GRAPH_NODE_NAMES,
    AGENT_GRAPH_RECURSION_LIMIT,
    AgentCheckpointThreadError,
    AgentWorkflow,
    build_agent_graph,
)
from app.agent.nodes.approval import AgentApprovalRequest, AgentApprovalResponse
from app.agent.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderTimeoutError,
)
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION, PlanningGoal
from app.agent.state import (
    AgentApprovalDecision,
    AgentGraphInput,
    AgentGraphOutput,
    AgentTerminalStatus,
)
from app.core.exceptions import TaskNotFoundError
from app.models import TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)


def _proposal_response(
    *,
    action_count: int = 0,
    summary: str = "A bounded study plan",
    valid_actions: bool = True,
) -> ProviderResponse:
    project_id = uuid4()
    actions: list[dict[str, object]] = []
    for index in range(action_count):
        arguments: dict[str, object] = {"project_id": str(project_id)}
        if valid_actions:
            arguments["title"] = f"Task {index + 1}"
        actions.append(
            {
                "action_key": f"create_{index + 1}",
                "tool_name": "create_task",
                "arguments": arguments,
            }
        )
    payload = {
        "planning_result": {
            "prompt_version": STUDY_PLAN_PROMPT_VERSION,
            "status": "completed",
            "plan": {
                "summary": summary,
                "steps": [
                    {
                        "step_key": "step_1",
                        "position": 1,
                        "title": "Study",
                        "description": "Complete one focused session",
                        "success_criteria": "Notes exist",
                    }
                ],
            },
        },
        "actions": actions,
    }
    return ProviderResponse(output_text=json.dumps(payload))


class CapturingProvider:
    def __init__(self, outcomes: Iterable[ProviderResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ProviderRequest] = []

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SequenceApprover:
    def __init__(self, decisions: Iterable[AgentApprovalResponse | Exception]) -> None:
        self.decisions = list(decisions)
        self.requests: list[AgentApprovalRequest] = []

    def decide(self, request: AgentApprovalRequest) -> AgentApprovalResponse:
        self.requests.append(request)
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision


class FakeGateway:
    def __init__(
        self,
        *,
        write_outcomes: Iterable[PublicTask | Exception] = (),
        context_failure: Exception | None = None,
    ) -> None:
        self.write_outcomes = list(write_outcomes)
        self.context_failure = context_failure
        self.calls: list[tuple[str, UUID]] = []

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        self.calls.append(("list_projects", user_id))
        if self.context_failure is not None:
            raise self.context_failure
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        self.calls.append(("list_tasks", user_id))
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        self.calls.append(("create_task", user_id))
        outcome = self.write_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        self.calls.append(("update_task", user_id))
        outcome = self.write_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _task(title: str = "Task") -> PublicTask:
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


def _workflow(
    *,
    provider: CapturingProvider,
    approver: SequenceApprover,
    gateway: FakeGateway,
    user_id: UUID | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> tuple[AgentWorkflow, UUID]:
    trusted_user_id = user_id or uuid4()
    workflow = build_agent_graph(
        model="synthetic-model",
        provider=provider,
        gateway=gateway,
        runtime_context=AgentRuntimeContext(
            user_id=trusted_user_id,
            write_tools_enabled=True,
        ),
        approval_decider=approver,
        clock=lambda: 1.0,
        sleeper=lambda _: None,
        checkpointer=checkpointer,
    )
    return workflow, trusted_user_id


def _approve() -> AgentApprovalResponse:
    return AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)


def _reject() -> AgentApprovalResponse:
    return AgentApprovalResponse(decision=AgentApprovalDecision.REJECTED)


def _change(feedback: str = "Make the plan smaller") -> AgentApprovalResponse:
    return AgentApprovalResponse(
        decision=AgentApprovalDecision.REQUEST_CHANGES,
        feedback=feedback,
    )


def test_graph_has_exact_eight_named_nodes_and_explicit_boundaries() -> None:
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response()]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(),
    )
    graph = workflow.get_graph()

    assert tuple(name for name in graph.nodes if not name.startswith("__")) == (
        AGENT_GRAPH_NODE_NAMES
    )
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("__start__", "analyze_goal") in edges
    assert ("execute_tasks", "verify_result") in edges
    assert ("verify_result", "summarize") in edges
    assert ("summarize", "__end__") in edges


def test_happy_no_action_path_returns_one_strict_public_output() -> None:
    provider = CapturingProvider([_proposal_response()])
    approver = SequenceApprover([_approve()])
    gateway = FakeGateway()
    workflow, user_id = _workflow(
        provider=provider,
        approver=approver,
        gateway=gateway,
    )

    output = workflow.invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Learn transactions"))
    )

    assert isinstance(output, AgentGraphOutput)
    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert output.execution_records == ()
    assert [name for name, _ in gateway.calls] == ["list_projects", "list_tasks"]
    assert all(identity == user_id for _, identity in gateway.calls)
    serialized = output.model_dump_json()
    assert str(user_id) not in serialized
    assert "user_id" not in serialized


def test_approved_action_uses_injected_gateway_and_public_result() -> None:
    gateway = FakeGateway(write_outcomes=[_task("Created")])
    workflow, user_id = _workflow(
        provider=CapturingProvider([_proposal_response(action_count=1)]),
        approver=SequenceApprover([_approve()]),
        gateway=gateway,
    )

    output = workflow.invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Create a study task"))
    )

    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert len(output.execution_records) == 1
    assert output.execution_records[0].result is not None
    assert gateway.calls[-1] == ("create_task", user_id)
    assert output.metrics is not None
    assert output.metrics.tool_call_count == 1


def test_rejected_plan_terminates_without_write() -> None:
    gateway = FakeGateway(write_outcomes=[_task()])
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response(action_count=1)]),
        approver=SequenceApprover([_reject()]),
        gateway=gateway,
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.REJECTED
    assert output.execution_records == ()
    assert all(name != "create_task" for name, _ in gateway.calls)


@pytest.mark.parametrize("change_count", [1, 2])
def test_one_or_two_edit_cycles_regenerate_revalidate_and_reapprove(
    change_count: int,
) -> None:
    provider = CapturingProvider(
        [
            _proposal_response(summary=f"Plan revision {index}")
            for index in range(change_count + 1)
        ]
    )
    feedback = [f"Change request {index}" for index in range(change_count)]
    approver = SequenceApprover([*(_change(item) for item in feedback), _approve()])
    workflow, _ = _workflow(
        provider=provider,
        approver=approver,
        gateway=FakeGateway(),
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert len(provider.requests) == change_count + 1
    assert len(approver.requests) == change_count + 1
    assert output.metrics is not None
    assert output.metrics.model_round_count == change_count + 1
    assert output.metrics.provider_attempt_count == change_count + 1
    assert [request.revision for request in approver.requests] == list(
        range(change_count + 1)
    )
    for index, item in enumerate(feedback, start=1):
        assert item in provider.requests[index].input


def test_third_edit_request_fails_closed_with_no_write() -> None:
    gateway = FakeGateway()
    workflow, _ = _workflow(
        provider=CapturingProvider(
            [_proposal_response(summary=f"Plan {index}") for index in range(3)]
        ),
        approver=SequenceApprover([_change(), _change(), _change()]),
        gateway=gateway,
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.REJECTED
    assert all(name not in {"create_task", "update_task"} for name, _ in gateway.calls)


def test_invalid_plan_terminates_before_approval_or_write() -> None:
    approver = SequenceApprover([_approve()])
    gateway = FakeGateway()
    workflow, _ = _workflow(
        provider=CapturingProvider(
            [_proposal_response(action_count=1, valid_actions=False)]
        ),
        approver=approver,
        gateway=gateway,
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.FAILED
    assert approver.requests == []
    assert all(name != "create_task" for name, _ in gateway.calls)


@pytest.mark.parametrize(
    ("provider", "approver", "gateway"),
    [
        (
            CapturingProvider(
                [ProviderTimeoutError("private"), ProviderTimeoutError("private")]
            ),
            SequenceApprover([_approve()]),
            FakeGateway(),
        ),
        (
            CapturingProvider([_proposal_response()]),
            SequenceApprover([_approve()]),
            FakeGateway(context_failure=RuntimeError("private")),
        ),
        (
            CapturingProvider([_proposal_response()]),
            SequenceApprover([RuntimeError("private")]),
            FakeGateway(),
        ),
    ],
    ids=["provider", "context", "approval"],
)
def test_provider_context_and_approval_failures_are_safe(
    provider: CapturingProvider,
    approver: SequenceApprover,
    gateway: FakeGateway,
) -> None:
    workflow, _ = _workflow(
        provider=provider,
        approver=approver,
        gateway=gateway,
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.FAILED
    assert output.summary in {
        "Plan execution failed safely.",
        AGENT_GRAPH_FAILURE_SUMMARY,
    }
    assert "private" not in output.model_dump_json()


@pytest.mark.parametrize(
    ("outcomes", "expected_status", "expected_records"),
    [
        ([TaskNotFoundError("private")], AgentTerminalStatus.FAILED, 1),
        (
            [_task("First"), TaskNotFoundError("private")],
            AgentTerminalStatus.PARTIAL_FAILURE,
            2,
        ),
    ],
    ids=["first-failure", "partial-failure"],
)
def test_tool_failures_are_truthful_and_not_leaked(
    outcomes: list[PublicTask | Exception],
    expected_status: AgentTerminalStatus,
    expected_records: int,
) -> None:
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response(action_count=len(outcomes))]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(write_outcomes=outcomes),
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is expected_status
    assert len(output.execution_records) == expected_records
    assert "private" not in output.model_dump_json()


def test_input_is_strict_and_output_excludes_runtime_dependencies() -> None:
    workflow, user_id = _workflow(
        provider=CapturingProvider([_proposal_response()]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(),
    )

    invalid_input: object = {
        "goal": {"objective": "Learn"},
        "user_id": str(user_id),
    }
    with pytest.raises(ValidationError):
        workflow.invoke(invalid_input)  # type: ignore[arg-type]

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))
    assert set(output.model_dump()) == {
        "status",
        "plan",
        "summary",
        "execution_records",
        "metrics",
    }
    serialized = output.model_dump_json()
    for forbidden in (
        str(user_id),
        '"user_id"',
        '"session"',
        '"repository"',
        '"provider"',
        '"approval_decider"',
        '"api_key"',
        '"access_token"',
        '"raw_response"',
        '"reasoning"',
    ):
        assert forbidden not in serialized.lower()


class RecursingCompiledGraph:
    def __init__(self) -> None:
        self.config: dict[str, object] | None = None

    def invoke(
        self,
        input: dict[str, object],
        config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.config = config
        raise GraphRecursionError("private graph diagnostic")

    def get_graph(self) -> object:
        raise AssertionError("not needed")


def test_recursion_ceiling_is_fixed_and_failure_is_redacted() -> None:
    compiled = RecursingCompiledGraph()
    workflow = AgentWorkflow(compiled)  # type: ignore[arg-type]

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert compiled.config == {"recursion_limit": AGENT_GRAPH_RECURSION_LIMIT}
    assert output.status is AgentTerminalStatus.FAILED
    assert output.summary == AGENT_GRAPH_FAILURE_SUMMARY
    assert "private" not in output.model_dump_json()


def test_checkpointed_graph_requires_a_host_owned_thread_id() -> None:
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response()]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(),
        checkpointer=InMemorySaver(),
    )

    with pytest.raises(AgentCheckpointThreadError, match="thread ID is required"):
        workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))


def test_checkpointed_graph_uses_exact_stable_thread_configuration() -> None:
    saver = InMemorySaver()
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response()]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(),
        checkpointer=saver,
    )
    thread_id = uuid4()

    output = workflow.invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Learn")),
        thread_id=thread_id,
    )

    assert output.status is AgentTerminalStatus.SUCCEEDED
    stored = saver.get_tuple({"configurable": {"thread_id": str(thread_id)}})
    assert stored is not None


def test_offline_graph_stays_compatible_without_thread_configuration() -> None:
    workflow, _ = _workflow(
        provider=CapturingProvider([_proposal_response()]),
        approver=SequenceApprover([_approve()]),
        gateway=FakeGateway(),
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.SUCCEEDED
