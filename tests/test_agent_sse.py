"""Public SSE framing, resume, ownership, and resource-lifetime contracts."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import agent_runs as endpoint
from app.core.exceptions import AGENT_RUN_NOT_FOUND_MESSAGE, AgentRunNotFoundError
from app.db.session import get_session
from app.main import app
from app.models.agent_run import (
    AgentApprovalStatus,
    AgentRunStatus,
)
from app.models.agent_tool_execution import AgentToolExecutionStatus
from app.models.user import User
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.agent_tool_executions import AgentToolExecutionRepository
from app.schemas.agent_events import (
    AGENT_EVENT_VERSION,
    HeartbeatPayload,
    PublicAgentEvent,
    PublicAgentEventType,
)
from app.schemas.agent_run import (
    AgentRunMetricsSnapshot,
    AgentRunNode,
    PublicAgentApproval,
    PublicAgentRun,
)
from app.schemas.agent_tool_execution import PublicAgentToolExecution
from app.services.agent_events import (
    AGENT_EVENT_CURSOR_MESSAGE,
    MAX_PUBLIC_TOOL_EXECUTIONS,
    AgentEventCursorError,
    build_public_agent_events,
    get_owned_agent_events,
    iter_sse_events,
    resume_after_event,
)

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _user() -> User:
    return User(
        id=uuid4(),
        email="sse@example.com",
        password_hash="synthetic-hash",
        created_at=NOW,
        updated_at=NOW,
    )


def _run(
    run_id: UUID,
    *,
    status: AgentRunStatus = AgentRunStatus.PENDING_APPROVAL,
) -> PublicAgentRun:
    terminal = status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.PARTIAL_FAILURE,
        AgentRunStatus.FAILED,
    }
    return PublicAgentRun(
        id=run_id,
        thread_id=uuid4(),
        status=status,
        current_node=(
            AgentRunNode.SUMMARIZE if terminal else AgentRunNode.REQUEST_APPROVAL
        ),
        summary="Safe final summary" if terminal else None,
        error_code="AGENT_FAILED" if status is AgentRunStatus.FAILED else None,
        prompt_version="study-plan.v1",
        metrics=AgentRunMetricsSnapshot(tool_call_count=1),
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=4),
    )


def _approval(run_id: UUID) -> PublicAgentApproval:
    return PublicAgentApproval(
        id=uuid4(),
        run_id=run_id,
        revision=0,
        proposal_fingerprint="a" * 64,
        decision=AgentApprovalStatus.PENDING,
        feedback=None,
        decided_at=None,
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    )


def _execution(
    run_id: UUID,
    *,
    status: AgentToolExecutionStatus = AgentToolExecutionStatus.COMPLETED,
) -> PublicAgentToolExecution:
    failed = status in {
        AgentToolExecutionStatus.FAILED,
        AgentToolExecutionStatus.UNKNOWN,
    }
    return PublicAgentToolExecution(
        id=uuid4(),
        run_id=run_id,
        revision=0,
        action_key="delete-once",
        tool_name="delete_task",
        status=status,
        attempt_count=1,
        result_task_id=None if failed else uuid4(),
        result_summary=None if failed else "Task deleted",
        error_code="AGENT_ACTION_FAILED" if failed else None,
        started_at=NOW + timedelta(seconds=2),
        completed_at=NOW + timedelta(seconds=3),
        created_at=NOW + timedelta(seconds=2),
        updated_at=NOW + timedelta(seconds=3),
    )


def _events(
    run_id: UUID | None = None,
    *,
    status: AgentRunStatus = AgentRunStatus.PENDING_APPROVAL,
) -> tuple[PublicAgentEvent, ...]:
    identifier = run_id or uuid4()
    return build_public_agent_events(
        _run(identifier, status=status),
        (_approval(identifier),) if status is AgentRunStatus.PENDING_APPROVAL else (),
        (_execution(identifier),),
    )


def test_retained_events_are_stable_ordered_bounded_and_redacted() -> None:
    run_id = uuid4()
    first = _events(run_id)
    second = _events(run_id)

    assert [event.event_type for event in first] == [
        PublicAgentEventType.APPROVAL_REQUIRED,
        PublicAgentEventType.TOOL_STARTED,
        PublicAgentEventType.TOOL_RESULT,
        PublicAgentEventType.RUN_STATUS,
        PublicAgentEventType.NODE_STATUS,
        PublicAgentEventType.METRICS,
        PublicAgentEventType.HEARTBEAT,
    ]
    assert [event.event_id for event in first] == [
        f"{run_id}:{sequence:08d}" for sequence in range(1, 8)
    ]
    assert [item.model_dump() for item in first] == [
        item.model_dump() for item in second
    ]
    assert all(event.occurred_at.tzinfo is UTC for event in first)
    heartbeat = first[-1]
    assert isinstance(heartbeat.payload, HeartbeatPayload)
    assert heartbeat.payload.model_dump() == {"kind": "heartbeat"}
    serialized = "".join(event.model_dump_json() for event in first)
    for forbidden in (
        "user_id",
        "checkpoint",
        "arguments",
        "authorization",
        "database_url",
        "hidden_reasoning",
        "synthetic-hash",
    ):
        assert forbidden not in serialized.lower()


@pytest.mark.parametrize(
    "status",
    [
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.PARTIAL_FAILURE,
        AgentRunStatus.FAILED,
    ],
)
def test_completed_run_has_exactly_one_terminal_event(status: AgentRunStatus) -> None:
    events = _events(status=status)
    assert (
        sum(
            event.event_type is PublicAgentEventType.TERMINAL_RESULT for event in events
        )
        == 1
    )
    assert all(
        event.event_type is not PublicAgentEventType.HEARTBEAT for event in events
    )


def test_failed_tool_projects_only_a_safe_error_code() -> None:
    run_id = uuid4()
    events = build_public_agent_events(
        _run(run_id),
        (_approval(run_id),),
        (_execution(run_id, status=AgentToolExecutionStatus.UNKNOWN),),
    )
    assert PublicAgentEventType.SAFE_ERROR in {event.event_type for event in events}
    assert PublicAgentEventType.TOOL_RESULT not in {
        event.event_type for event in events
    }
    serialized = "".join(event.model_dump_json() for event in events)
    assert "AGENT_ACTION_FAILED" in serialized
    assert "diagnostic" not in serialized.lower()


def test_event_service_uses_owner_scoped_bounded_short_reads() -> None:
    run_id, user_id = uuid4(), uuid4()
    session = MagicMock(spec=Session)
    run_repository = MagicMock(spec=AgentRunRepository)
    execution_repository = MagicMock(spec=AgentToolExecutionRepository)
    run_repository.get_owned_run.return_value = _run(run_id)
    run_repository.list_owned_approvals.return_value = (_approval(run_id),)
    execution_repository.list_owned_run_actions.return_value = (_execution(run_id),)

    events = get_owned_agent_events(
        run_id,
        user_id,
        session,
        run_repository_factory=lambda received: cast(
            AgentRunRepository, run_repository
        ),
        execution_repository_factory=lambda received: cast(
            AgentToolExecutionRepository, execution_repository
        ),
    )

    assert events
    run_repository.get_owned_run.assert_called_once_with(run_id=run_id, user_id=user_id)
    run_repository.list_owned_approvals.assert_called_once_with(
        run_id=run_id, user_id=user_id
    )
    execution_repository.list_owned_run_actions.assert_called_once_with(
        run_id=run_id,
        user_id=user_id,
        limit=MAX_PUBLIC_TOOL_EXECUTIONS,
    )
    session.commit.assert_not_called()
    session.rollback.assert_not_called()

    run_repository.get_owned_run.return_value = None
    with pytest.raises(AgentRunNotFoundError, match=AGENT_RUN_NOT_FOUND_MESSAGE):
        get_owned_agent_events(
            run_id,
            user_id,
            session,
            run_repository_factory=lambda received: cast(
                AgentRunRepository, run_repository
            ),
            execution_repository_factory=lambda received: cast(
                AgentToolExecutionRepository, execution_repository
            ),
        )


def test_cursor_resumes_after_exact_event_and_fails_closed_otherwise() -> None:
    events = _events()
    assert resume_after_event(events, events[1].event_id) == events[2:]
    for cursor in (
        "invalid",
        f"{uuid4()}:00000001",
        f"{events[0].run_id}:99999999",
    ):
        with pytest.raises(AgentEventCursorError) as error:
            resume_after_event(events, cursor)
        assert str(error.value) == AGENT_EVENT_CURSOR_MESSAGE
        assert cursor not in str(error.value)


def test_schema_rejects_naive_time_mismatched_type_id_and_long_payload() -> None:
    run_id = uuid4()
    base = {
        "event_id": f"{run_id}:00000001",
        "sequence": 1,
        "run_id": run_id,
        "event_type": "run_status",
        "occurred_at": NOW,
        "payload": {"kind": "run_status", "status": "RUNNING"},
    }
    for invalid in (
        {**base, "occurred_at": datetime(2026, 9, 5)},
        {**base, "event_id": f"{run_id}:00000002"},
        {**base, "event_type": "heartbeat"},
        {
            **base,
            "event_type": "tool_result",
            "payload": {
                "kind": "tool_result",
                "tool_name": "delete_task",
                "status": "COMPLETED",
                "summary": "x" * 301,
            },
        },
    ):
        with pytest.raises(ValidationError):
            PublicAgentEvent.model_validate(invalid)


def test_sse_framing_and_generator_close_callback() -> None:
    events = _events()
    closed = False

    def mark_closed() -> None:
        nonlocal closed
        closed = True

    stream = iter_sse_events(events, on_close=mark_closed)
    first_frame = next(stream)
    assert first_frame.startswith(f"id: {events[0].event_id}\n")
    assert f"event: {events[0].event_type.value}\n" in first_frame
    data = next(
        line.removeprefix("data: ")
        for line in first_frame.splitlines()
        if line.startswith("data: ")
    )
    assert json.loads(data)["version"] == AGENT_EVENT_VERSION
    assert first_frame.endswith("\n\n")
    stream.close()
    assert closed is True


@pytest.fixture
def sse_client() -> Iterator[tuple[TestClient, User, MagicMock]]:
    user = _user()
    session = MagicMock(spec=Session)

    def session_dependency() -> Iterator[Session]:
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session_dependency
    try:
        with TestClient(app) as client:
            yield client, user, session
    finally:
        app.dependency_overrides.clear()


def test_sse_endpoint_closes_session_before_framing_and_supports_resume(
    sse_client: tuple[TestClient, User, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, session = sse_client
    run_id = uuid4()
    events = _events(run_id)
    service = MagicMock(return_value=events)
    real_framer = iter_sse_events

    def checked_framer(selected: tuple[PublicAgentEvent, ...]) -> Iterator[str]:
        assert session.close.called
        yield from real_framer(selected)

    monkeypatch.setattr(endpoint, "get_owned_agent_events", service)
    monkeypatch.setattr(endpoint, "iter_sse_events", checked_framer)
    response = client.get(
        f"/api/v1/agent/runs/{run_id}/events",
        headers={"Last-Event-ID": events[1].event_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.count("\nid: ") + response.text.startswith("id: ") == len(
        events[2:]
    )
    assert events[0].event_id not in response.text
    service.assert_called_once_with(run_id, user.id, session)


def test_sse_endpoint_maps_owner_404_and_cursor_conflict(
    sse_client: tuple[TestClient, User, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user_value, _session = sse_client
    run_id = uuid4()
    service = MagicMock(side_effect=AgentRunNotFoundError("private"))
    monkeypatch.setattr(endpoint, "get_owned_agent_events", service)
    missing = client.get(f"/api/v1/agent/runs/{run_id}/events")
    assert missing.status_code == 404
    assert missing.json() == {"detail": AGENT_RUN_NOT_FOUND_MESSAGE}
    assert "private" not in missing.text

    service.side_effect = None
    service.return_value = _events(run_id)
    conflict = client.get(
        f"/api/v1/agent/runs/{run_id}/events",
        headers={"Last-Event-ID": "invalid"},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": AGENT_EVENT_CURSOR_MESSAGE}
    assert "invalid" not in conflict.text


def test_sse_route_requires_authentication_and_openapi_is_narrow(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    try:
        response = client.get(f"/api/v1/agent/runs/{uuid4()}/events")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    operation = app.openapi()["paths"]["/api/v1/agent/runs/{run_id}/events"]
    assert set(operation) == {"get"}
    assert set(operation["get"]["responses"]) == {"200", "401", "404", "409", "422"}
