"""HTTP contracts for durable Agent runs and approval decisions."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import agent_runs as endpoint
from app.core.exceptions import (
    AGENT_RUN_CONFLICT_MESSAGE,
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentWorkflowUnavailableError,
)
from app.db.session import get_session
from app.main import app
from app.models.agent_run import AgentRunStatus, AgentThreadStatus
from app.models.user import User
from app.schemas.agent_run import (
    AgentRunMetricsSnapshot,
    AgentRunNode,
    AgentRunSnapshot,
    PublicAgentRun,
    PublicAgentThread,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _user() -> User:
    return User(
        id=uuid4(),
        email="agent@example.com",
        password_hash="synthetic-hash",
        created_at=NOW,
        updated_at=NOW,
    )


def _snapshot(run_id: UUID | None = None) -> AgentRunSnapshot:
    thread_id = uuid4()
    return AgentRunSnapshot(
        thread=PublicAgentThread(
            id=thread_id,
            goal_summary="Learn durable workflows",
            status=AgentThreadStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=PublicAgentRun(
            id=run_id or uuid4(),
            thread_id=thread_id,
            status=AgentRunStatus.PENDING_APPROVAL,
            current_node=AgentRunNode.REQUEST_APPROVAL,
            summary=None,
            error_code=None,
            prompt_version="study-plan.v1",
            metrics=AgentRunMetricsSnapshot(),
            created_at=NOW,
            updated_at=NOW,
        ),
    )


@pytest.fixture
def agent_client() -> Iterator[tuple[TestClient, User, Session]]:
    user = _user()
    session = MagicMock(spec=Session)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            yield client, user, session
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_agent_client(client: TestClient) -> Iterator[TestClient]:
    """Keep the authentication contract test independent of database config."""

    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/agent/runs", {"goal": {"objective": "Learn"}}),
        ("get", f"/api/v1/agent/runs/{uuid4()}", None),
        ("get", f"/api/v1/agent/runs/{uuid4()}/events", None),
        (
            "post",
            f"/api/v1/agent/runs/{uuid4()}/approval",
            {
                "revision": 0,
                "proposal_fingerprint": "a" * 64,
                "decision": "APPROVED",
            },
        ),
    ],
)
def test_all_agent_routes_require_bearer_authentication(
    unauthenticated_agent_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = unauthenticated_agent_client.request(method, path, json=payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_start_delegates_trusted_identity_and_returns_201(
    agent_client: tuple[TestClient, User, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, user, session = agent_client
    expected = _snapshot()
    service = MagicMock(return_value=expected)
    monkeypatch.setattr(endpoint, "start_agent_run", service)

    response = client.post(
        "/api/v1/agent/runs",
        json={"goal": {"objective": "Learn durable workflows"}},
    )

    assert response.status_code == 201
    service.assert_called_once()
    assert service.call_args.args[1:] == (user.id, session)
    assert set(response.json()) == {"thread", "run", "approval"}
    assert "user_id" not in response.text
    assert "checkpoint" not in response.text.lower()


def test_read_delegates_owned_run_and_maps_safe_404(
    agent_client: tuple[TestClient, User, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, user, session = agent_client
    run_id = uuid4()
    service = MagicMock(return_value=_snapshot(run_id))
    monkeypatch.setattr(endpoint, "get_agent_run_snapshot", service)
    assert client.get(f"/api/v1/agent/runs/{run_id}").status_code == 200
    service.assert_called_once_with(run_id, user.id, session)

    service.side_effect = AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    missing = client.get(f"/api/v1/agent/runs/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": AGENT_RUN_NOT_FOUND_MESSAGE}


def test_approval_delegates_strict_input_and_maps_conflict(
    agent_client: tuple[TestClient, User, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, user, session = agent_client
    run_id = uuid4()
    service = MagicMock(return_value=_snapshot(run_id))
    monkeypatch.setattr(endpoint, "submit_agent_approval", service)
    payload = {
        "revision": 0,
        "proposal_fingerprint": "a" * 64,
        "decision": "REQUEST_CHANGES",
        "feedback": "  Shorter  ",
    }
    response = client.post(f"/api/v1/agent/runs/{run_id}/approval", json=payload)
    assert response.status_code == 200
    submission = service.call_args.args[1]
    assert submission.feedback == "Shorter"
    assert service.call_args.args[2:] == (user.id, session)

    service.side_effect = AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)
    conflict = client.post(
        f"/api/v1/agent/runs/{run_id}/approval",
        json={**payload, "decision": "APPROVED", "feedback": None},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": AGENT_RUN_CONFLICT_MESSAGE}


def test_strict_inputs_reject_identity_and_invalid_decisions_before_service(
    agent_client: tuple[TestClient, User, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _user_value, _session = agent_client
    start = MagicMock()
    decide = MagicMock()
    monkeypatch.setattr(endpoint, "start_agent_run", start)
    monkeypatch.setattr(endpoint, "submit_agent_approval", decide)

    bad_start = client.post(
        "/api/v1/agent/runs",
        json={"goal": {"objective": "Learn"}, "user_id": str(uuid4())},
    )
    bad_decision = client.post(
        f"/api/v1/agent/runs/{uuid4()}/approval",
        json={
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "PENDING",
        },
    )
    assert bad_start.status_code == bad_decision.status_code == 422
    start.assert_not_called()
    decide.assert_not_called()


def test_start_and_resume_map_infrastructure_failure_to_safe_503(
    agent_client: tuple[TestClient, User, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _user_value, _session = agent_client
    monkeypatch.setattr(
        endpoint,
        "start_agent_run",
        MagicMock(side_effect=AgentWorkflowUnavailableError("private")),
    )
    response = client.post("/api/v1/agent/runs", json={"goal": {"objective": "Learn"}})
    assert response.status_code == 503
    assert response.json() == {"detail": AGENT_WORKFLOW_UNAVAILABLE_MESSAGE}
    assert "private" not in response.text

    monkeypatch.setattr(
        endpoint,
        "submit_agent_approval",
        MagicMock(side_effect=AgentWorkflowUnavailableError("private")),
    )
    resumed = client.post(
        f"/api/v1/agent/runs/{uuid4()}/approval",
        json={
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "APPROVED",
        },
    )
    assert resumed.status_code == 503
    assert resumed.json() == {"detail": AGENT_WORKFLOW_UNAVAILABLE_MESSAGE}
    assert "private" not in resumed.text


def test_openapi_exposes_only_public_agent_contracts() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/agent/runs"]) == {"post"}
    assert set(paths["/api/v1/agent/runs/{run_id}"]) == {"get"}
    assert set(paths["/api/v1/agent/runs/{run_id}/events"]) == {"get"}
    assert set(paths["/api/v1/agent/runs/{run_id}/approval"]) == {"post"}
    assert set(paths["/api/v1/agent/runs"]["post"]["responses"]) == {
        "201",
        "401",
        "422",
        "503",
    }
    assert set(paths["/api/v1/agent/runs/{run_id}"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "422",
    }
    assert set(paths["/api/v1/agent/runs/{run_id}/events"]["get"]["responses"]) == {
        "200",
        "401",
        "404",
        "409",
        "422",
    }
    assert set(paths["/api/v1/agent/runs/{run_id}/approval"]["post"]["responses"]) == {
        "200",
        "401",
        "404",
        "409",
        "422",
        "503",
    }
    schemas = str(app.openapi()["components"]["schemas"]).lower()
    for forbidden in ("checkpoint_id", "database_url", "authorization"):
        assert forbidden not in schemas
