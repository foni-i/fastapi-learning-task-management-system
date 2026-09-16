"""Real PostgreSQL proof for authenticated durable Agent interrupt/resume."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Never
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.agent.checkpointing import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.graph import AgentWorkflow, build_agent_graph
from app.agent.nodes.approval import AgentApprovalRequest, ApprovalDecider
from app.agent.providers import ProviderRequest, ProviderResponse
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION
from app.agent.tools import AgentToolGateway
from app.api.v1.endpoints import agent_runs as endpoint
from app.core.tokens import create_access_token
from app.db.session import get_session
from app.main import app
from app.models import AgentApproval, AgentRun, AgentThread, User
from app.schemas.agent_run import (
    AgentApprovalSubmission,
    AgentRunSnapshot,
    AgentRunStartRequest,
)
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery, KnowledgeSearchResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.agent_workflow import (
    start_agent_run as real_start_agent_run,
)
from app.services.agent_workflow import (
    submit_agent_approval as real_submit_agent_approval,
)

pytestmark = pytest.mark.integration


class StaticProvider:
    """Return one deterministic valid proposal without external I/O."""

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        del request, timeout_seconds
        return ProviderResponse(
            output_text=json.dumps(
                {
                    "planning_result": {
                        "prompt_version": STUDY_PLAN_PROMPT_VERSION,
                        "status": "completed",
                        "plan": {
                            "summary": "A safe persisted plan",
                            "steps": [
                                {
                                    "step_key": "step_1",
                                    "position": 1,
                                    "title": "Study",
                                    "description": "Complete one focused session",
                                    "success_criteria": "A short note exists",
                                }
                            ],
                        },
                    },
                    "actions": [],
                }
            )
        )


class EmptyGateway(AgentToolGateway):
    """Supply deterministic owner-scoped context without domain writes."""

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        del user_id, include_archived
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        del user_id
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def search_knowledge(
        self,
        *,
        user_id: UUID,
        search_query: KnowledgeSearchQuery,
    ) -> KnowledgeSearchResult:
        del user_id, search_query
        return KnowledgeSearchResult(items=())

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        del user_id, task_input
        raise AssertionError("No write action was approved")

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        del user_id, task_id, task_update
        raise AssertionError("No write action was approved")


class InterruptOnlyApproval(ApprovalDecider):
    def decide(self, request: AgentApprovalRequest) -> Never:
        del request
        raise AssertionError("Durable graph must use LangGraph interrupt")


class DurableWorkflowFactory:
    """Rebuild the workflow and checkpoint connection on every service call."""

    def __init__(self, checkpoint_url: URL) -> None:
        self._database_url = SecretStr(
            checkpoint_url.render_as_string(hide_password=False)
        )
        self.open_count = 0

    @contextmanager
    def __call__(self, user_id: UUID) -> Iterator[AgentWorkflow]:
        self.open_count += 1
        with open_postgres_checkpointer(
            database_url=self._database_url,
            setup=False,
        ) as checkpointer:
            yield build_agent_graph(
                model="synthetic-test-model",
                provider=StaticProvider(),
                gateway=EmptyGateway(),
                runtime_context=AgentRuntimeContext(
                    user_id=user_id,
                    write_tools_enabled=True,
                ),
                approval_decider=InterruptOnlyApproval(),
                clock=monotonic,
                sleeper=lambda _: None,
                checkpointer=checkpointer,
                durable_approval=True,
            )


def _create_user(factory: sessionmaker[Session]) -> User:
    user = User(
        id=uuid4(),
        email=f"agent-{uuid4().hex}@example.com",
        password_hash="synthetic-integration-hash",
    )
    with factory.begin() as session:
        session.add(user)
    return user


def _bearer(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_authenticated_run_resumes_after_process_local_workflow_loss(
    integration_engine: Engine,
    test_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_name = f"agent_resume_{uuid4().hex}"
    checkpoint_url = test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    with integration_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    checkpoint_secret = SecretStr(checkpoint_url.render_as_string(hide_password=False))
    with open_postgres_checkpointer(
        database_url=checkpoint_secret,
        setup=True,
    ):
        pass

    session_factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(session_factory)
    other_user = _create_user(session_factory)
    opened_sessions: list[Session] = []
    closed_sessions: list[Session] = []
    workflow_factory = DurableWorkflowFactory(checkpoint_url)

    def session_dependency() -> Iterator[Session]:
        session = session_factory()
        opened_sessions.append(session)
        try:
            yield session
        finally:
            session.close()
            closed_sessions.append(session)

    def start_with_durable_workflow(
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

    def resume_with_durable_workflow(
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
    monkeypatch.setattr(endpoint, "start_agent_run", start_with_durable_workflow)
    monkeypatch.setattr(endpoint, "submit_agent_approval", resume_with_durable_workflow)
    run_id: UUID | None = None
    try:
        with TestClient(app) as client:
            started = client.post(
                "/api/v1/agent/runs",
                headers=_bearer(owner.id),
                json={"goal": {"objective": "Build a durable study plan"}},
            )
            assert started.status_code == 201
            started_body = started.json()
            assert set(started_body) == {"thread", "run", "approval"}
            assert started_body["run"]["status"] == "PENDING_APPROVAL"
            assert started_body["approval"]["decision"] == "PENDING"
            assert "user_id" not in started.text
            assert "checkpoint" not in started.text.casefold()
            run_id = UUID(started_body["run"]["id"])

            foreign = client.get(
                f"/api/v1/agent/runs/{run_id}",
                headers=_bearer(other_user.id),
            )
            assert foreign.status_code == 404

            approval = started_body["approval"]
            approved = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert approved.status_code == 200
            assert approved.json()["run"]["status"] == "SUCCEEDED"
            assert approved.json()["approval"]["decision"] == "APPROVED"

            duplicate = client.post(
                f"/api/v1/agent/runs/{run_id}/approval",
                headers=_bearer(owner.id),
                json={
                    "revision": approval["revision"],
                    "proposal_fingerprint": approval["proposal_fingerprint"],
                    "decision": "APPROVED",
                },
            )
            assert duplicate.status_code == 200
            assert duplicate.json() == approved.json()

        assert workflow_factory.open_count == 3
        assert len(opened_sessions) == 4
        assert {id(item) for item in opened_sessions} == {
            id(item) for item in closed_sessions
        }
        assert run_id is not None
        with session_factory() as session:
            run = session.scalar(select(AgentRun).where(AgentRun.id == run_id))
            assert run is not None
            assert run.status == "SUCCEEDED"
            approval_record = session.scalar(
                select(AgentApproval).where(AgentApproval.run_id == run_id)
            )
            assert approval_record is not None
            assert approval_record.decision == "APPROVED"
    finally:
        app.dependency_overrides.pop(get_session, None)
        with session_factory.begin() as session:
            session.execute(
                delete(AgentApproval).where(
                    AgentApproval.user_id.in_((owner.id, other_user.id))
                )
            )
            session.execute(
                delete(AgentRun).where(AgentRun.user_id.in_((owner.id, other_user.id)))
            )
            session.execute(
                delete(AgentThread).where(
                    AgentThread.user_id.in_((owner.id, other_user.id))
                )
            )
            session.execute(delete(User).where(User.id.in_((owner.id, other_user.id))))
        with integration_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))
