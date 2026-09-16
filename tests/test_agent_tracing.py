"""Offline contracts for content-free Agent tracing and failure isolation."""

import json
from collections.abc import Callable, Iterable
from itertools import count
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.events import AgentProgressEvent
from app.agent.graph import AgentWorkflow, build_agent_graph
from app.agent.nodes.approval import AgentApprovalRequest, AgentApprovalResponse
from app.agent.providers import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderPermissionError,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUsage,
)
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION, PlanningGoal
from app.agent.state import (
    AgentApprovalDecision,
    AgentGraphInput,
    AgentGraphState,
    AgentTerminalStatus,
)
from app.agent.tracing import (
    MAX_TRACE_ATTEMPTS,
    MAX_TRACE_LATENCY_MS,
    MAX_TRACE_TOKENS,
    NOOP_TRACE_SINK,
    TRACE_SCHEMA_VERSION,
    AgentTraceEvent,
    TraceComponent,
    TraceErrorCode,
    TraceOutcome,
    TracePhase,
    emit_trace,
)
from app.models.agent_run import AgentRun
from app.schemas.agent_events import PublicAgentEvent
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery, KnowledgeSearchResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from tests.fakes.tracing import RecordingTraceSink


def _proposal_response() -> ProviderResponse:
    payload = {
        "planning_result": {
            "prompt_version": STUDY_PLAN_PROMPT_VERSION,
            "status": "completed",
            "plan": {
                "summary": "Safe plan",
                "steps": [
                    {
                        "step_key": "step_1",
                        "position": 1,
                        "title": "Study",
                        "description": "Read",
                        "success_criteria": "Notes exist",
                        "citation_ids": [],
                    }
                ],
            },
        },
        "actions": [],
    }
    return ProviderResponse(
        output_text=json.dumps(payload),
        usage=ProviderUsage(input_tokens=11, output_tokens=7, total_tokens=18),
    )


class ScriptedProvider:
    def __init__(self, outcomes: Iterable[ProviderResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ProviderRequest] = []

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        del timeout_seconds
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class EmptyGateway:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[str] = []

    def list_projects(
        self, *, user_id: UUID, page: int, page_size: int, include_archived: bool
    ) -> ProjectListResponse:
        del user_id, include_archived
        self.calls.append("list_projects")
        if self.failure is not None:
            raise self.failure
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(self, *, user_id: UUID, query: TaskListQuery) -> TaskListResponse:
        del user_id
        self.calls.append("list_tasks")
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def search_knowledge(
        self, *, user_id: UUID, search_query: KnowledgeSearchQuery
    ) -> KnowledgeSearchResult:
        del user_id, search_query
        self.calls.append("search_knowledge")
        return KnowledgeSearchResult(items=())

    def create_task(self, *, user_id: UUID, task_input: TaskCreate) -> PublicTask:
        raise AssertionError((user_id, task_input))

    def update_task(
        self, *, user_id: UUID, task_id: UUID, task_update: TaskUpdate
    ) -> PublicTask:
        raise AssertionError((user_id, task_id, task_update))


class Approver:
    def decide(self, request: AgentApprovalRequest) -> AgentApprovalResponse:
        del request
        return AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)


def _clock() -> Callable[[], float]:
    ticks = count()
    return lambda: next(ticks) / 1000


def _workflow(
    *,
    sink: RecordingTraceSink,
    provider: ScriptedProvider,
    gateway: EmptyGateway,
    run_id: UUID,
) -> AgentWorkflow:
    return build_agent_graph(
        model="synthetic-model",
        provider=provider,
        gateway=gateway,
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
        approval_decider=Approver(),
        clock=lambda: 1.0,
        sleeper=lambda _: None,
        trace_sink=sink,
        trace_run_id=run_id,
        trace_clock=_clock(),
    )


def test_trace_schema_is_exact_bounded_immutable_and_json_serializable() -> None:
    run_id = uuid4()
    event = AgentTraceEvent(
        run_id=run_id,
        component=TraceComponent.PROVIDER,
        name="model_provider",
        phase=TracePhase.FINISHED,
        outcome=TraceOutcome.SUCCEEDED,
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        attempt=1,
        model_round_count=1,
        provider_attempt_count=1,
        tool_call_count=3,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=12.5,
    )

    assert event.version == TRACE_SCHEMA_VERSION
    assert AgentTraceEvent.model_validate_json(event.model_dump_json()) == event
    assert set(event.model_dump()) == {
        "version",
        "run_id",
        "thread_id",
        "component",
        "name",
        "phase",
        "outcome",
        "error_code",
        "prompt_version",
        "attempt",
        "model_round_count",
        "provider_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }
    for invalid in (
        {**event.model_dump(), "payload": {"goal": "private"}},
        {**event.model_dump(), "attempt": MAX_TRACE_ATTEMPTS + 1},
        {**event.model_dump(), "input_tokens": MAX_TRACE_TOKENS + 1},
        {**event.model_dump(), "latency_ms": MAX_TRACE_LATENCY_MS + 1},
        {**event.model_dump(), "latency_ms": -1},
        {**event.model_dump(), "name": "private-user-controlled-name"},
    ):
        with pytest.raises(ValidationError):
            AgentTraceEvent.model_validate(invalid)
    with pytest.raises(ValidationError):
        event.latency_ms = 0


def test_noop_and_sink_failure_never_change_graph_control_flow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id, thread_id = uuid4(), uuid4()
    normal_provider = ScriptedProvider([_proposal_response()])
    normal_gateway = EmptyGateway()
    normal = _workflow(
        sink=RecordingTraceSink(),
        provider=normal_provider,
        gateway=normal_gateway,
        run_id=run_id,
    ).invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Safe goal")), thread_id=thread_id
    )

    failing_sink = RecordingTraceSink(fail=True)
    failed_provider = ScriptedProvider([_proposal_response()])
    failed_gateway = EmptyGateway()
    with_sink_failure = _workflow(
        sink=failing_sink,
        provider=failed_provider,
        gateway=failed_gateway,
        run_id=run_id,
    ).invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Safe goal")), thread_id=thread_id
    )

    assert with_sink_failure == normal
    assert len(normal_provider.requests) == len(failed_provider.requests) == 1
    assert (
        normal_gateway.calls
        == failed_gateway.calls
        == [
            "list_projects",
            "list_tasks",
            "search_knowledge",
        ]
    )
    assert failing_sink.calls > 0
    assert "synthetic private sink diagnostic" not in caplog.text
    emit_trace(
        NOOP_TRACE_SINK,
        AgentTraceEvent(
            run_id=run_id,
            component=TraceComponent.RUN,
            name="agent_workflow",
            phase=TracePhase.STARTED,
        ),
    )


def test_sink_failure_does_not_cover_a_safe_graph_failure() -> None:
    run_id = uuid4()
    normal_provider = ScriptedProvider([_proposal_response()])
    normal_gateway = EmptyGateway(failure=RuntimeError("private database detail"))
    normal = _workflow(
        sink=RecordingTraceSink(),
        provider=normal_provider,
        gateway=normal_gateway,
        run_id=run_id,
    ).invoke(AgentGraphInput(goal=PlanningGoal(objective="Safe goal")))

    failing_provider = ScriptedProvider([_proposal_response()])
    failing_gateway = EmptyGateway(failure=RuntimeError("private database detail"))
    with_sink_failure = _workflow(
        sink=RecordingTraceSink(fail=True),
        provider=failing_provider,
        gateway=failing_gateway,
        run_id=run_id,
    ).invoke(AgentGraphInput(goal=PlanningGoal(objective="Safe goal")))

    assert with_sink_failure == normal
    assert normal.status is AgentTerminalStatus.FAILED
    assert normal_gateway.calls == failing_gateway.calls == ["list_projects"]
    assert normal_provider.requests == failing_provider.requests == []


def test_graph_traces_stable_run_node_tool_provider_order_and_counts() -> None:
    run_id, thread_id = uuid4(), uuid4()
    sink = RecordingTraceSink()
    provider = ScriptedProvider(
        [ProviderTimeoutError("private timeout detail"), _proposal_response()]
    )
    gateway = EmptyGateway()

    output = _workflow(
        sink=sink,
        provider=provider,
        gateway=gateway,
        run_id=run_id,
    ).invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Do not trace this goal")),
        thread_id=thread_id,
    )

    assert output.status is AgentTerminalStatus.SUCCEEDED
    assert len(provider.requests) == 2
    assert all(event.run_id == run_id for event in sink.events)
    run_events = [
        event for event in sink.events if event.component is TraceComponent.RUN
    ]
    assert [(event.phase, event.outcome) for event in run_events] == [
        (TracePhase.STARTED, None),
        (TracePhase.FINISHED, TraceOutcome.SUCCEEDED),
    ]
    assert all(event.thread_id == thread_id for event in run_events)
    provider_events = [
        event for event in sink.events if event.component is TraceComponent.PROVIDER
    ]
    assert [
        (event.phase, event.attempt, event.error_code) for event in provider_events
    ] == [
        (TracePhase.STARTED, 1, None),
        (TracePhase.FAILED, 1, TraceErrorCode.PROVIDER_TIMEOUT),
        (TracePhase.STARTED, 2, None),
        (TracePhase.FINISHED, 2, None),
    ]
    assert provider_events[-1].total_tokens == 18
    assert run_events[-1].provider_attempt_count == 2
    assert run_events[-1].total_tokens == 18
    for component in (TraceComponent.NODE, TraceComponent.TOOL):
        events = [event for event in sink.events if event.component is component]
        assert events
        for index, event in enumerate(events):
            if event.phase is TracePhase.STARTED:
                terminal = next(
                    later
                    for later in events[index + 1 :]
                    if later.name == event.name
                    and later.phase in {TracePhase.FINISHED, TracePhase.FAILED}
                )
                assert terminal.latency_ms is not None

    repeated_sink = RecordingTraceSink()
    repeated_output = _workflow(
        sink=repeated_sink,
        provider=ScriptedProvider(
            [ProviderTimeoutError("different private detail"), _proposal_response()]
        ),
        gateway=EmptyGateway(),
        run_id=run_id,
    ).invoke(
        AgentGraphInput(goal=PlanningGoal(objective="Do not trace this goal")),
        thread_id=thread_id,
    )
    assert repeated_output == output
    assert [event.model_dump() for event in repeated_sink.events] == [
        event.model_dump() for event in sink.events
    ]


@pytest.mark.parametrize(
    ("provider_error", "expected_code", "retryable"),
    [
        (ProviderTimeoutError("private"), TraceErrorCode.PROVIDER_TIMEOUT, True),
        (ProviderTransientError("private"), TraceErrorCode.PROVIDER_TRANSIENT, True),
        (
            ProviderInvalidResponseError("private"),
            TraceErrorCode.PROVIDER_INVALID_RESPONSE,
            True,
        ),
        (
            ProviderConfigurationError("private"),
            TraceErrorCode.PROVIDER_CONFIGURATION,
            False,
        ),
        (
            ProviderAuthenticationError("private"),
            TraceErrorCode.PROVIDER_AUTHENTICATION,
            False,
        ),
        (
            ProviderPermissionError("private"),
            TraceErrorCode.PROVIDER_PERMISSION,
            False,
        ),
        (RuntimeError("private"), TraceErrorCode.PROVIDER_FAILURE, False),
    ],
)
def test_provider_failures_map_only_to_allowlisted_codes(
    provider_error: Exception,
    expected_code: TraceErrorCode,
    retryable: bool,
) -> None:
    sink = RecordingTraceSink()
    outcomes: list[ProviderResponse | Exception] = [provider_error]
    if retryable:
        outcomes.append(_proposal_response())
    provider = ScriptedProvider(outcomes)

    output = _workflow(
        sink=sink,
        provider=provider,
        gateway=EmptyGateway(),
        run_id=uuid4(),
    ).invoke(AgentGraphInput(goal=PlanningGoal(objective="Safe goal")))

    failures = [
        event
        for event in sink.events
        if event.component is TraceComponent.PROVIDER
        and event.phase is TracePhase.FAILED
    ]
    assert len(failures) == 1
    assert failures[0].error_code is expected_code
    assert "private" not in failures[0].model_dump_json()
    assert len(provider.requests) == (2 if retryable else 1)
    assert output.status is (
        AgentTerminalStatus.SUCCEEDED if retryable else AgentTerminalStatus.FAILED
    )


def test_failures_use_safe_codes_and_traces_are_separate_from_product_state() -> None:
    sensitive = (
        "goal API_KEY Authorization postgresql://password SQL vector excerpt "
        "hidden_reasoning stack trace"
    )
    run_id = uuid4()
    sink = RecordingTraceSink()
    gateway = EmptyGateway(failure=RuntimeError(sensitive))
    output = _workflow(
        sink=sink,
        provider=ScriptedProvider([_proposal_response()]),
        gateway=gateway,
        run_id=run_id,
    ).invoke(AgentGraphInput(goal=PlanningGoal(objective=sensitive)))

    assert output.status is AgentTerminalStatus.FAILED
    serialized = "".join(event.model_dump_json() for event in sink.events).casefold()
    for forbidden in (
        "api_key",
        "authorization",
        "postgresql://",
        "password",
        " sql ",
        "vector",
        "excerpt",
        "hidden_reasoning",
        "stack trace",
    ):
        assert forbidden not in serialized
    assert any(
        event.phase is TracePhase.FAILED
        and event.error_code is TraceErrorCode.TOOL_EXECUTION_FAILED
        for event in sink.events
    )
    assert "trace" not in AgentGraphState.model_fields
    assert "trace" not in AgentProgressEvent.model_fields
    assert "trace" not in PublicAgentEvent.model_fields
    assert "trace" not in AgentRun.__table__.columns
