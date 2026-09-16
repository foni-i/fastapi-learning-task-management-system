"""End-to-end PostgreSQL recovery through the public Agent HTTP boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.agent.checkpointing import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.graph import AgentWorkflow, build_agent_graph
from app.api.v1.endpoints import agent_runs as endpoint
from app.core.exceptions import TaskNotFoundError
from app.db.session import get_session
from app.main import app
from app.models import AgentApproval, AgentRun, AgentThread, User
from app.schemas.agent_run import (
    AgentApprovalSubmission,
    AgentRunSnapshot,
    AgentRunStartRequest,
)
from app.schemas.task import PublicTask
from app.services.agent_workflow import start_agent_run as real_start_agent_run
from app.services.agent_workflow import (
    submit_agent_approval as real_submit_agent_approval,
)
from tests.integration.test_agent_interrupt_resume import (
    DurableWorkflowFactory,
    InterruptOnlyApproval,
    _bearer,
    _create_user,
)
from tests.test_agent_graph import (
    CapturingProvider,
    FakeGateway,
    _proposal_response,
    _task,
)

pytestmark = pytest.mark.integration


def _sse_events(response_text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for frame in response_text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append(fields)
    return events


class FailureWorkflowFactory:
    """Rebuild a checkpointed runtime that produces a truthful safe failure."""

    def __init__(self, checkpoint_url: URL, *, partial: bool) -> None:
        self._checkpoint_url = checkpoint_url
        self._partial = partial
        self.open_count = 0

    @contextmanager
    def __call__(self, user_id: UUID) -> Iterator[AgentWorkflow]:
        self.open_count += 1
        outcomes: list[PublicTask | Exception] = (
            [_task("Created before failure"), TaskNotFoundError("private")]
            if self._partial
            else [TaskNotFoundError("private")]
        )
        with open_postgres_checkpointer(
            database_url=SecretStr(
                self._checkpoint_url.render_as_string(hide_password=False)
            ),
            setup=False,
        ) as checkpointer:
            yield build_agent_graph(
                model="synthetic-test-model",
                provider=CapturingProvider([_proposal_response(action_count=2)]),
                gateway=FakeGateway(write_outcomes=outcomes),
                runtime_context=AgentRuntimeContext(
                    user_id=user_id,
                    write_tools_enabled=True,
                ),
                approval_decider=InterruptOnlyApproval(),
                clock=lambda: 1.0,
                sleeper=lambda _: None,
                checkpointer=checkpointer,
                durable_approval=True,
            )


def test_public_run_recovers_after_runtime_rebuild_and_streams_safe_history(
    integration_engine: Engine,
    test_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_name = f"agent_final_recovery_{uuid4().hex}"
    checkpoint_url = test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    with integration_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    with open_postgres_checkpointer(
        database_url=SecretStr(checkpoint_url.render_as_string(hide_password=False)),
        setup=True,
    ):
        pass

    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(factory)
    other_user = _create_user(factory)
    opened: list[Session] = []
    closed: list[Session] = []
    runtime_factories: list[DurableWorkflowFactory] = []

    def session_dependency() -> Iterator[Session]:
        session = factory()
        opened.append(session)
        try:
            yield session
        finally:
            session.close()
            closed.append(session)

    @contextmanager
    def rebuilt_workflow(user_id: UUID) -> Iterator[AgentWorkflow]:
        runtime = DurableWorkflowFactory(checkpoint_url)
        runtime_factories.append(runtime)
        with runtime(user_id) as workflow:
            yield workflow

    def start_with_rebuilt_runtime(
        request: AgentRunStartRequest,
        user_id: UUID,
        session: Session,
    ) -> AgentRunSnapshot:
        return real_start_agent_run(
            request,
            user_id,
            session,
            workflow_factory=rebuilt_workflow,
        )

    def resume_with_rebuilt_runtime(
        run_id: UUID,
        submission: AgentApprovalSubmission,
        user_id: UUID,
        session: Session,
    ) -> AgentRunSnapshot:
        return real_submit_agent_approval(
            run_id,
            submission,
            user_id,
            session,
            workflow_factory=rebuilt_workflow,
        )

    app.dependency_overrides[get_session] = session_dependency
    monkeypatch.setattr(endpoint, "start_agent_run", start_with_rebuilt_runtime)
    monkeypatch.setattr(endpoint, "submit_agent_approval", resume_with_rebuilt_runtime)
    owned_run_ids: list[UUID] = []
    try:
        with TestClient(app) as client:
            started = client.post(
                "/api/v1/agent/runs",
                headers=_bearer(owner.id),
                json={"goal": {"objective": "Build a restart-safe plan"}},
            )
            assert started.status_code == 201
            first = started.json()
            run_id = UUID(first["run"]["id"])
            owned_run_ids.append(run_id)
            thread_id = first["thread"]["id"]
            approval = first["approval"]

            foreign = client.get(
                f"/api/v1/agent/runs/{run_id}",
                headers=_bearer(other_user.id),
            )
            assert foreign.status_code == 404

            requested = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "REQUEST_CHANGES",
                    "feedback": "Use a smaller plan",
                },
            )
            assert requested.status_code == 200
            revised = requested.json()
            assert revised["thread"]["id"] == thread_id
            assert revised["run"]["id"] == str(run_id)
            assert revised["approval"]["revision"] == 1
            assert revised["approval"]["decision"] == "PENDING"

            feedback_mismatch = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "REQUEST_CHANGES",
                    "feedback": "Use a different revision",
                },
            )
            assert feedback_mismatch.status_code == 409

            fingerprint_mismatch = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": "f" * 64,
                    "decision": "REQUEST_CHANGES",
                    "feedback": "Use a smaller plan",
                },
            )
            assert fingerprint_mismatch.status_code == 409

            foreign_approval = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(other_user.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "REQUEST_CHANGES",
                    "feedback": "Use a smaller plan",
                },
            )
            assert foreign_approval.status_code == 404

            stale = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert stale.status_code == 409

            revised_approval = revised["approval"]
            approved = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": revised_approval["revision"],
                    "proposal_fingerprint": revised_approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert approved.status_code == 200
            assert approved.json()["run"]["status"] == "SUCCEEDED"

            duplicate = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": revised_approval["revision"],
                    "proposal_fingerprint": revised_approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert duplicate.status_code == 200
            assert duplicate.json() == approved.json()

            stream = client.get(
                f"/api/v1/agent/runs/{run_id}/events",
                headers=_bearer(owner.id),
            )
            assert stream.status_code == 200
            events = _sse_events(stream.text)
            assert sum(item["event"] == "terminal_result" for item in events) == 1
            cursor = events[-2]["id"]
            resumed = client.get(
                f"/api/v1/agent/runs/{run_id}/events",
                headers={**_bearer(owner.id), "Last-Event-ID": str(cursor)},
            )
            resumed_events = _sse_events(resumed.text)
            assert [item["id"] for item in resumed_events] == [events[-1]["id"]]

            rejected_start = client.post(
                "/api/v1/agent/runs",
                headers=_bearer(owner.id),
                json={"goal": {"objective": "Reject this durable plan"}},
            )
            rejected_body = rejected_start.json()
            rejected_run_id = UUID(rejected_body["run"]["id"])
            owned_run_ids.append(rejected_run_id)
            rejected_approval = rejected_body["approval"]
            rejected = client.post(
                f"/api/v1/agent/runs/{rejected_run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": rejected_approval["revision"],
                    "proposal_fingerprint": rejected_approval["proposal_fingerprint"],
                    "decision": "REJECTED",
                },
            )
            assert rejected.status_code == 200
            assert rejected.json()["run"]["status"] == "REJECTED"

        assert len(runtime_factories) == 6
        assert all(runtime.open_count == 1 for runtime in runtime_factories)
        assert {id(session) for session in opened} == {
            id(session) for session in closed
        }
        combined = started.text + requested.text + approved.text + stream.text
        for forbidden in (
            "user_id",
            "checkpoint",
            "hidden_reasoning",
            "authorization",
            "database_url",
            "synthetic-integration-hash",
        ):
            assert forbidden not in combined.casefold()
    finally:
        app.dependency_overrides.pop(get_session, None)
        with factory.begin() as session:
            session.execute(
                delete(AgentApproval).where(AgentApproval.user_id == owner.id)
            )
            session.execute(delete(AgentRun).where(AgentRun.user_id == owner.id))
            session.execute(delete(AgentThread).where(AgentThread.user_id == owner.id))
            session.execute(delete(User).where(User.id.in_((owner.id, other_user.id))))
        with integration_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))


@pytest.mark.parametrize(
    ("partial", "expected_status"),
    [(False, "FAILED"), (True, "PARTIAL_FAILURE")],
    ids=["first-failure", "partial-failure"],
)
def test_rebuilt_runtime_persists_truthful_safe_failure_status(
    partial: bool,
    expected_status: str,
    integration_engine: Engine,
    test_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_name = f"agent_final_failure_{uuid4().hex}"
    checkpoint_url = test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    with integration_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    with open_postgres_checkpointer(
        database_url=SecretStr(checkpoint_url.render_as_string(hide_password=False)),
        setup=True,
    ):
        pass

    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(factory)
    workflow_factory = FailureWorkflowFactory(checkpoint_url, partial=partial)

    def session_dependency() -> Iterator[Session]:
        with factory() as session:
            yield session

    def start_with_failure_runtime(
        request: AgentRunStartRequest,
        user_id: UUID,
        session: Session,
    ) -> AgentRunSnapshot:
        return real_start_agent_run(
            request,
            user_id,
            session,
            workflow_factory=workflow_factory,
        )

    def resume_with_failure_runtime(
        run_id: UUID,
        submission: AgentApprovalSubmission,
        user_id: UUID,
        session: Session,
    ) -> AgentRunSnapshot:
        return real_submit_agent_approval(
            run_id,
            submission,
            user_id,
            session,
            workflow_factory=workflow_factory,
        )

    app.dependency_overrides[get_session] = session_dependency
    monkeypatch.setattr(endpoint, "start_agent_run", start_with_failure_runtime)
    monkeypatch.setattr(endpoint, "submit_agent_approval", resume_with_failure_runtime)
    try:
        with TestClient(app) as client:
            started = client.post(
                "/api/v1/agent/runs",
                headers=_bearer(owner.id),
                json={"goal": {"objective": "Exercise a safe failure"}},
            )
            assert started.status_code == 201
            body = started.json()
            run_id = UUID(body["run"]["id"])
            approval = body["approval"]
            completed = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert completed.status_code == 200
            assert completed.json()["run"]["status"] == expected_status

            read_back = client.get(
                f"/api/v1/agent/runs/{run_id}",
                headers=_bearer(owner.id),
            )
            assert read_back.status_code == 200
            assert read_back.json()["run"]["status"] == expected_status
            stream = client.get(
                f"/api/v1/agent/runs/{run_id}/events",
                headers=_bearer(owner.id),
            )
            assert (
                sum(
                    event["event"] == "terminal_result"
                    for event in _sse_events(stream.text)
                )
                == 1
            )
            assert "private" not in (completed.text + stream.text).casefold()
        assert workflow_factory.open_count == 2
    finally:
        app.dependency_overrides.pop(get_session, None)
        with factory.begin() as session:
            session.execute(
                delete(AgentApproval).where(AgentApproval.user_id == owner.id)
            )
            session.execute(delete(AgentRun).where(AgentRun.user_id == owner.id))
            session.execute(delete(AgentThread).where(AgentThread.user_id == owner.id))
            session.execute(delete(User).where(User.id == owner.id))
        with integration_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))
