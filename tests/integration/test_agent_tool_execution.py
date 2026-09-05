"""Real PostgreSQL migration, claim race, and replay proof for Task 10.5."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from time import monotonic
from typing import cast
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, delete, func, inspect, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.agent.context import AgentRuntimeContext
from app.agent.state import AgentProposedAction, AgentWriteToolName
from app.agent.tools import AgentToolResult, execute_tool
from app.core.config import get_settings
from app.core.exceptions import AgentToolReconciliationRequiredError
from app.models import (
    AgentRun,
    AgentThread,
    AgentToolExecution,
    Project,
    Task,
    User,
)
from app.repositories.agent_tool_executions import AgentToolExecutionRepository
from app.schemas.task import PublicTask
from app.services.agent_domain import AgentDomainGateway
from app.services.agent_tool_executions import AgentToolExecutionCoordinator
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PRE_IDEMPOTENCY_REVISION = "21ec26a7c672"
IDEMPOTENCY_REVISION = "8b7d4e2f1a90"


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_idempotency_migration_round_trip_retains_task_10_1_tables(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = migration_test_database_url.render_as_string(hide_password=False)
    config = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("STMS_DATABASE_URL", target)
    get_settings.cache_clear()
    try:
        validate_migration_test_target(target)
        command.downgrade(config, PRE_IDEMPOTENCY_REVISION)
        assert _current_revision(integration_engine) == PRE_IDEMPOTENCY_REVISION
        tables = set(inspect(integration_engine).get_table_names())
        assert "agent_tool_executions" not in tables
        assert {"agent_threads", "agent_runs", "agent_approvals"} <= tables

        command.upgrade(config, IDEMPOTENCY_REVISION)
        assert _current_revision(integration_engine) == IDEMPOTENCY_REVISION
        inspector = inspect(integration_engine)
        assert "agent_tool_executions" in inspector.get_table_names()
        columns = {
            item["name"]: item
            for item in inspector.get_columns("agent_tool_executions")
        }
        assert set(columns) == {
            "id",
            "run_id",
            "user_id",
            "revision",
            "proposal_fingerprint",
            "action_key",
            "tool_name",
            "status",
            "attempt_count",
            "result_task_id",
            "result_summary",
            "error_code",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        }
        assert inspector.get_pk_constraint("agent_tool_executions")["name"] == (
            "pk_agent_tool_executions"
        )
        for name in ("started_at", "completed_at", "created_at", "updated_at"):
            assert isinstance(columns[name]["type"], DateTime)
            assert cast(DateTime, columns[name]["type"]).timezone is True
        assert {
            item["name"]
            for item in inspector.get_unique_constraints("agent_tool_executions")
        } == {"uq_agent_tool_executions_action_identity"}
        assert {
            item["name"] for item in inspector.get_foreign_keys("agent_tool_executions")
        } == {"fk_agent_tool_executions_run_id_user_id_agent_runs"}
        assert {
            item["name"]
            for item in inspector.get_check_constraints("agent_tool_executions")
        } == {
            "ck_agent_tool_executions_action_key",
            "ck_agent_tool_executions_attempt_count",
            "ck_agent_tool_executions_error_code",
            "ck_agent_tool_executions_proposal_fingerprint",
            "ck_agent_tool_executions_revision",
            "ck_agent_tool_executions_state",
            "ck_agent_tool_executions_status",
            "ck_agent_tool_executions_tool_name",
        }
        assert {
            item["name"]
            for item in inspector.get_indexes("agent_tool_executions")
            if item.get("duplicates_constraint") is None
        } == {
            "ix_agent_tool_executions_run_id",
            "ix_agent_tool_executions_user_id",
            "ix_agent_tool_executions_user_status",
        }

        command.downgrade(config, PRE_IDEMPOTENCY_REVISION)
        assert _current_revision(integration_engine) == PRE_IDEMPOTENCY_REVISION
        assert (
            "agent_tool_executions" not in inspect(integration_engine).get_table_names()
        )
        assert {"agent_threads", "agent_runs", "agent_approvals"} <= set(
            inspect(integration_engine).get_table_names()
        )
        command.upgrade(config, IDEMPOTENCY_REVISION)
        assert _current_revision(integration_engine) == IDEMPOTENCY_REVISION
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()


class BarrierClaimRepository(AgentToolExecutionRepository):
    barrier: Barrier

    def create_claim(self, **values: object) -> AgentToolExecution:
        self.barrier.wait(timeout=5)
        return super().create_claim(**values)  # type: ignore[arg-type]


def test_two_session_claim_race_writes_once_and_completed_replay_calls_no_tool(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    user = User(
        email=f"agent-idempotency-{uuid4().hex}@example.com",
        password_hash="synthetic-integration-hash",
    )
    with factory.begin() as session:
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Idempotency project")
        session.add(project)
        session.flush()
        thread = AgentThread(user_id=user.id, goal_summary="Create one task")
        session.add(thread)
        session.flush()
        run = AgentRun(
            thread_id=thread.id,
            user_id=user.id,
            prompt_version="study-plan-v1",
        )
        session.add(run)
        session.flush()

    action = AgentProposedAction(
        action_key="create-once",
        tool_name=AgentWriteToolName.CREATE_TASK,
        arguments={
            "project_id": str(project.id),
            "title": "Created exactly once",
            "priority": "MEDIUM",
        },
    )
    context = AgentRuntimeContext(user_id=user.id, write_tools_enabled=True)
    barrier = Barrier(2)
    BarrierClaimRepository.barrier = barrier
    tool_calls = 0
    tool_lock = Lock()

    def repository_factory(session: Session) -> AgentToolExecutionRepository:
        return BarrierClaimRepository(session)

    real_dispatcher = execute_tool

    def counted_dispatcher(*args: object, **kwargs: object) -> AgentToolResult:
        nonlocal tool_calls
        with tool_lock:
            tool_calls += 1
        return real_dispatcher(*args, **kwargs)  # type: ignore[arg-type]

    def coordinator() -> AgentToolExecutionCoordinator:
        return AgentToolExecutionCoordinator(
            run_id=run.id,
            user_id=user.id,
            gateway=AgentDomainGateway(session_factory=factory),
            session_factory=factory,
            repository_factory=repository_factory,
            dispatcher=counted_dispatcher,
        )

    def attempt() -> object:
        try:
            return coordinator()(
                action,
                revision=0,
                proposal_fingerprint="e" * 64,
                runtime_context=context,
            )
        except AgentToolReconciliationRequiredError as error:
            return error

    try:
        started = monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        assert monotonic() - started < 10
        assert tool_calls == 1
        assert (
            sum(
                isinstance(item, AgentToolReconciliationRequiredError)
                for item in outcomes
            )
            <= 1
        )

        replay = coordinator()(
            action,
            revision=0,
            proposal_fingerprint="e" * 64,
            runtime_context=context,
        )
        assert isinstance(replay, PublicTask)
        assert replay.title == "Created exactly once"
        assert tool_calls == 1

        with factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.user_id == user.id, Task.title == "Created exactly once"
                    )
                )
                == 1
            )
            executions = list(
                session.scalars(
                    select(AgentToolExecution).where(
                        AgentToolExecution.run_id == run.id
                    )
                )
            )
            assert len(executions) == 1
            assert executions[0].status == "COMPLETED"
            assert isinstance(replay, PublicTask)
            assert executions[0].result_task_id == replay.id
            assert not hasattr(executions[0], "arguments")
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AgentToolExecution).where(AgentToolExecution.run_id == run.id)
            )
            session.execute(delete(Task).where(Task.user_id == user.id))
            session.execute(delete(AgentRun).where(AgentRun.id == run.id))
            session.execute(delete(AgentThread).where(AgentThread.id == thread.id))
            session.execute(delete(Project).where(Project.id == project.id))
            session.execute(delete(User).where(User.id == user.id))
