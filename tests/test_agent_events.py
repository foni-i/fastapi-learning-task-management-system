"""Offline tests for transport-neutral safe Agent progress events."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.events import (
    AgentEventKind,
    AgentEventOutcome,
    AgentProgressEvent,
    stream_agent,
)
from app.agent.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderStreamChunk,
    ProviderToolCall,
)
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)

FINAL_RESULT = """{"prompt_version":"study-plan.v2","status":"completed","plan":{"summary":"Safe plan","steps":[{"step_key":"step_1","position":1,"title":"Study","description":"Read the chapter","success_criteria":"Notes exist"}]}}"""


class StreamingFake:
    def __init__(self, rounds: list[list[ProviderStreamChunk] | Exception]) -> None:
        self.rounds = rounds
        self.closed = 0

    @contextmanager
    def stream(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> Iterator[Iterator[ProviderStreamChunk]]:
        outcome = self.rounds.pop(0)
        try:
            if isinstance(outcome, Exception):
                raise outcome
            yield iter(outcome)
        finally:
            self.closed += 1


class EmptyGateway:
    def list_projects(
        self, *, user_id: UUID, page: int, page_size: int, include_archived: bool
    ) -> ProjectListResponse:
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(self, *, user_id: UUID, query: TaskListQuery) -> TaskListResponse:
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(self, *, user_id: UUID, task_input: TaskCreate) -> PublicTask:
        raise AssertionError("not used")

    def update_task(
        self, *, user_id: UUID, task_id: UUID, task_update: TaskUpdate
    ) -> PublicTask:
        raise AssertionError("not used")


def ticking_clock() -> Iterator[datetime]:
    current = datetime(2026, 9, 3, tzinfo=UTC)
    while True:
        yield current
        current += timedelta(milliseconds=1)


def test_stream_has_ordered_utc_events_and_same_validated_result() -> None:
    split = len(FINAL_RESULT) // 2
    provider = StreamingFake(
        [
            [
                ProviderStreamChunk(output_text_delta=FINAL_RESULT[:split]),
                ProviderStreamChunk(output_text_delta=FINAL_RESULT[split:]),
                ProviderStreamChunk(
                    response=ProviderResponse(output_text=FINAL_RESULT)
                ),
            ]
        ]
    )
    times = ticking_clock()
    events = list(
        stream_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=provider,
            gateway=EmptyGateway(),
            clock=lambda: next(times),
        )
    )
    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.MODEL_STARTED,
        AgentEventKind.RESULT_READY,
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert all(event.timestamp.tzinfo is UTC for event in events)
    assert events[-1].result is not None
    assert events[-1].result.plan.summary == "Safe plan"
    assert provider.closed == 1


def test_tool_progress_contains_name_but_no_payload_or_identity() -> None:
    provider = StreamingFake(
        [
            [
                ProviderStreamChunk(
                    response=ProviderResponse(
                        tool_calls=(
                            ProviderToolCall(
                                call_id="c1", name="list_tasks", arguments={}
                            ),
                        )
                    )
                )
            ],
            [ProviderStreamChunk(response=ProviderResponse(output_text=FINAL_RESULT))],
        ]
    )
    user_id = uuid4()
    events = list(
        stream_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=user_id),
            model="model",
            provider=provider,
            gateway=EmptyGateway(),
        )
    )
    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.MODEL_STARTED,
        AgentEventKind.TOOL_STARTED,
        AgentEventKind.TOOL_FINISHED,
        AgentEventKind.MODEL_STARTED,
        AgentEventKind.RESULT_READY,
    ]
    serialized = "".join(event.model_dump_json() for event in events)
    assert "list_tasks" in serialized
    for forbidden in (str(user_id), "arguments", "Validated tool result data"):
        assert forbidden not in serialized


def test_stream_failure_has_exactly_one_safe_terminal_event() -> None:
    secret = "synthetic-provider-secret-diagnostic"
    events = list(
        stream_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=StreamingFake([RuntimeError(secret)]),
            gateway=EmptyGateway(),
        )
    )
    assert events[-1].kind is AgentEventKind.RUN_FAILED
    assert events[-1].outcome is AgentEventOutcome.FAILED
    assert (
        sum(
            event.kind in {AgentEventKind.RUN_FAILED, AgentEventKind.RESULT_READY}
            for event in events
        )
        == 1
    )
    assert secret not in events[-1].model_dump_json()


def test_event_contract_rejects_naive_time_wrong_shape_and_long_summary() -> None:
    base = {
        "sequence": 1,
        "timestamp": datetime(2026, 9, 3, tzinfo=UTC),
        "summary": "Safe",
    }
    for payload in (
        {**base, "timestamp": datetime(2026, 9, 3), "kind": "run_started"},
        {**base, "kind": "tool_started"},
        {**base, "kind": "run_started", "tool_name": "list_tasks"},
        {**base, "kind": "run_started", "summary": "x" * 301},
        {**base, "kind": "result_ready", "outcome": "succeeded"},
    ):
        with pytest.raises(ValidationError):
            AgentProgressEvent.model_validate(payload)


def test_event_json_round_trip_and_fields_are_strict() -> None:
    event = AgentProgressEvent(
        sequence=1,
        timestamp=datetime(2026, 9, 3, tzinfo=UTC),
        kind=AgentEventKind.RUN_FAILED,
        summary="Safe failure",
        outcome=AgentEventOutcome.FAILED,
    )
    assert AgentProgressEvent.model_validate_json(event.model_dump_json()) == event
    assert set(event.model_dump()) == {
        "sequence",
        "timestamp",
        "kind",
        "stage",
        "tool_name",
        "summary",
        "outcome",
        "usage",
        "result",
        "metrics",
    }
