"""Offline integration tests for the bounded eight-node LangGraph workflow."""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.graph import (
    AGENT_GRAPH_FAILURE_SUMMARY,
    AGENT_GRAPH_NODE_NAMES,
    AGENT_GRAPH_RECURSION_LIMIT,
    AgentCheckpointStatus,
    AgentCheckpointThreadError,
    AgentWorkflow,
    build_agent_graph,
)
from app.agent.nodes.approval import AgentApprovalRequest, AgentApprovalResponse
from app.agent.nodes.finalization import verify_result
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
    AgentGraphState,
    AgentTerminalStatus,
)
from app.core.exceptions import TaskNotFoundError
from app.models import TaskPriority, TaskStatus
from app.schemas.knowledge_retrieval import (
    KnowledgeCitation,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)
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
    citation_ids: tuple[str, ...] = (),
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
                        "citation_ids": citation_ids,
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
        knowledge_result: KnowledgeSearchResult | None = None,
    ) -> None:
        self.write_outcomes = list(write_outcomes)
        self.context_failure = context_failure
        self.knowledge_result = knowledge_result or KnowledgeSearchResult(items=())
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

    def search_knowledge(
        self,
        *,
        user_id: UUID,
        search_query: KnowledgeSearchQuery,
    ) -> KnowledgeSearchResult:
        self.calls.append(("search_knowledge", user_id))
        return self.knowledge_result

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
    durable_approval: bool = False,
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
        durable_approval=durable_approval,
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
    assert [name for name, _ in gateway.calls] == [
        "list_projects",
        "list_tasks",
        "search_knowledge",
    ]
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


def test_grounded_plan_uses_only_retrieved_citation_before_approval() -> None:
    document_id, chunk_id = uuid4(), uuid4()
    citation_id = f"knowledge:{document_id}:{chunk_id}"
    gateway = FakeGateway(
        knowledge_result=KnowledgeSearchResult(
            items=(
                KnowledgeCitation(
                    citation_id=citation_id,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    source="evidence.txt",
                    page_number=1,
                    ordinal=0,
                    distance=None,
                    vector_rank=None,
                    lexical_rank=1,
                    fusion_score=1 / 61,
                    excerpt="Untrusted evidence",
                ),
            )
        )
    )
    provider = CapturingProvider([_proposal_response(citation_ids=(citation_id,))])
    approver = SequenceApprover([_approve()])
    workflow, _ = _workflow(
        provider=provider,
        approver=approver,
        gateway=gateway,
    )

    output = workflow.invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Use my notes"))
    )

    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert output.plan is not None
    assert output.plan.plan.steps[0].citation_ids == (citation_id,)
    assert len(approver.requests) == 1
    assert citation_id in provider.requests[0].input
    assert "Untrusted evidence" not in output.model_dump_json()
    assert "Untrusted evidence" not in approver.requests[0].model_dump_json()


def test_fabricated_citation_fails_before_approval_or_write() -> None:
    fabricated = f"knowledge:{uuid4()}:{uuid4()}"
    approver = SequenceApprover([_approve()])
    gateway = FakeGateway(write_outcomes=[_task()])
    workflow, _ = _workflow(
        provider=CapturingProvider(
            [_proposal_response(action_count=1, citation_ids=(fabricated,))]
        ),
        approver=approver,
        gateway=gateway,
    )

    output = workflow.invoke(AgentGraphInput(goal=PlanningGoal(objective="Learn")))

    assert output.status is AgentTerminalStatus.FAILED
    assert approver.requests == []
    assert all(name != "create_task" for name, _ in gateway.calls)
    assert fabricated not in output.model_dump_json()


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


def test_durable_graph_interrupts_with_safe_exact_approval_payload() -> None:
    workflow, user_id = _workflow(
        provider=CapturingProvider([_proposal_response(action_count=1)]),
        approver=SequenceApprover([]),
        gateway=FakeGateway(),
        checkpointer=InMemorySaver(),
        durable_approval=True,
    )

    thread_id = uuid4()
    progress = workflow.start_durable(
        AgentGraphInput(goal=PlanningGoal(objective="Create a task")),
        thread_id=thread_id,
    )
    inspection = workflow.inspect_durable(thread_id=thread_id)

    assert progress.output is None
    assert progress.approval is not None
    assert inspection.status is AgentCheckpointStatus.PENDING_INTERRUPT
    assert inspection.approval == progress.approval
    assert inspection.output is None
    assert set(progress.approval.model_dump()) == {
        "plan_summary",
        "action_names",
        "action_count",
        "revision",
        "proposal_fingerprint",
        "preview",
    }
    assert progress.approval.preview.proposal_fingerprint == (
        progress.approval.proposal_fingerprint
    )
    assert progress.approval.preview.actions[0].tool_name.value == "create_task"
    serialized = progress.approval.model_dump_json().lower()
    assert str(user_id) not in serialized
    for forbidden in ("user_id", "arguments", "checkpoint", "reasoning", "token"):
        assert forbidden not in serialized


def test_durable_approve_resumes_to_execution_and_reject_has_no_write() -> None:
    for decision, expected in (
        (_approve(), AgentTerminalStatus.SUCCEEDED),
        (_reject(), AgentTerminalStatus.REJECTED),
    ):
        gateway = FakeGateway(write_outcomes=[_task()])
        workflow, _ = _workflow(
            provider=CapturingProvider([_proposal_response(action_count=1)]),
            approver=SequenceApprover([]),
            gateway=gateway,
            checkpointer=InMemorySaver(),
            durable_approval=True,
        )
        thread_id = uuid4()
        workflow.start_durable(
            AgentGraphInput(goal=PlanningGoal(objective="Create a task")),
            thread_id=thread_id,
        )

        completed = workflow.resume_durable(decision, thread_id=thread_id)
        inspection = workflow.inspect_durable(thread_id=thread_id)

        assert completed.output is not None
        assert completed.output.status is expected
        assert inspection.status is AgentCheckpointStatus.TERMINAL
        assert inspection.output == completed.output
        writes = [name for name, _ in gateway.calls if name == "create_task"]
        assert len(writes) == (
            1 if decision.decision is AgentApprovalDecision.APPROVED else 0
        )


def test_durable_request_changes_creates_a_fresh_interrupt_revision() -> None:
    workflow, _ = _workflow(
        provider=CapturingProvider(
            [_proposal_response(summary="First"), _proposal_response(summary="Second")]
        ),
        approver=SequenceApprover([]),
        gateway=FakeGateway(),
        checkpointer=InMemorySaver(),
        durable_approval=True,
    )
    thread_id = uuid4()
    first = workflow.start_durable(
        AgentGraphInput(goal=PlanningGoal(objective="Revise a plan")),
        thread_id=thread_id,
    )

    second = workflow.resume_durable(_change("Make it shorter"), thread_id=thread_id)

    assert first.approval is not None
    assert second.approval is not None
    assert first.approval.revision == 0
    assert second.approval.revision == 1
    assert first.approval.proposal_fingerprint != second.approval.proposal_fingerprint


def test_public_snapshot_continuation_uses_none_input_and_reaches_terminal() -> None:
    saver = InMemorySaver()
    builder = StateGraph(AgentGraphState, input_schema=AgentGraphInput)
    builder.add_node(
        "prepare",
        lambda _state: {"workflow_error_code": "SPIKE_CONTINUATION"},
    )
    builder.add_node(
        "finish",
        lambda state: dict(verify_result(state)),
    )
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "finish")
    builder.add_edge("finish", END)
    compiled = builder.compile(checkpointer=saver)
    thread_id = uuid4()
    config = {"configurable": {"thread_id": str(thread_id)}}
    compiled.invoke(  # type: ignore[call-overload]
        AgentGraphInput(goal=PlanningGoal(objective="Continue")).model_dump(
            mode="python"
        ),
        config=config,
        interrupt_after=["prepare"],
    )
    workflow = AgentWorkflow(
        compiled,  # type: ignore[arg-type]
        checkpointing_enabled=True,
        durable_approval_enabled=True,
    )

    before = workflow.inspect_durable(thread_id=thread_id)
    completed = workflow.continue_durable(thread_id=thread_id)
    after = workflow.inspect_durable(thread_id=thread_id)

    assert before.status is AgentCheckpointStatus.CONTINUABLE
    assert completed.output is not None
    assert completed.output.status is AgentTerminalStatus.FAILED
    assert after.status is AgentCheckpointStatus.TERMINAL


def test_checkpoint_inspection_redacts_inconsistent_raw_values() -> None:
    class InconsistentCompiled:
        def get_state(self, _config: dict[str, object]) -> object:
            return SimpleNamespace(
                interrupts=(),
                next=(),
                values={"private_checkpoint_value": "must-not-escape"},
            )

    workflow = AgentWorkflow(
        InconsistentCompiled(),  # type: ignore[arg-type]
        checkpointing_enabled=True,
        durable_approval_enabled=True,
    )

    inspection = workflow.inspect_durable(thread_id=uuid4())

    assert inspection.status is AgentCheckpointStatus.INCONSISTENT
    serialized = repr(inspection).casefold()
    assert "private_checkpoint_value" not in serialized
    assert "must-not-escape" not in serialized
