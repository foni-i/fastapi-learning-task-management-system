"""PostgreSQL proof for the corrected high-impact Tool-name contract."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.agent.context import AgentRuntimeContext
from app.agent.state import AgentProposedAction, AgentWriteToolName
from app.agent.tools import AgentToolResult, execute_tool
from app.core.config import get_settings
from app.models import (
    AgentApproval,
    AgentRun,
    AgentThread,
    AgentToolExecution,
    Project,
    Task,
    User,
)
from app.models.agent_run import AgentApprovalStatus
from app.schemas.agent_tool import AgentToolMutationResult
from app.services.agent_domain import AgentDomainGateway
from app.services.agent_tool_executions import AgentToolExecutionCoordinator
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PRE_EXPANSION_REVISION = "8b7d4e2f1a90"
EXPANSION_REVISION = "c4d8a1f6e205"


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _tool_constraint(engine: Engine) -> str:
    constraints = inspect(engine).get_check_constraints("agent_tool_executions")
    return next(
        item["sqltext"]
        for item in constraints
        if item["name"] == "ck_agent_tool_executions_tool_name"
    )


def test_tool_name_constraint_migration_round_trip(
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
        command.downgrade(config, PRE_EXPANSION_REVISION)
        assert _current_revision(integration_engine) == PRE_EXPANSION_REVISION
        assert "agent_tool_executions" in inspect(integration_engine).get_table_names()
        old_constraint = _tool_constraint(integration_engine)
        assert "create_task" in old_constraint
        assert "update_task" in old_constraint
        assert "batch_create_tasks" not in old_constraint
        assert "delete_task" not in old_constraint

        factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
        user, project, run = _run_records(factory)
        try:
            with factory.begin() as session:
                session.add(
                    AgentToolExecution(
                        run_id=run.id,
                        user_id=user.id,
                        revision=0,
                        proposal_fingerprint="d" * 64,
                        action_key="old-allowed",
                        tool_name="create_task",
                    )
                )
            with factory() as session:
                session.add(
                    AgentToolExecution(
                        run_id=run.id,
                        user_id=user.id,
                        revision=0,
                        proposal_fingerprint="d" * 64,
                        action_key="new-rejected",
                        tool_name="delete_task",
                    )
                )
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
        finally:
            _cleanup(factory, user_id=user.id, project_id=project.id)

        command.upgrade(config, EXPANSION_REVISION)
        assert _current_revision(integration_engine) == EXPANSION_REVISION
        expanded = _tool_constraint(integration_engine)
        for tool_name in (
            "create_task",
            "update_task",
            "batch_create_tasks",
            "delete_task",
        ):
            assert tool_name in expanded
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()


def _run_records(factory: sessionmaker[Session]) -> tuple[User, Project, AgentRun]:
    user = User(
        email=f"agent-tool-contract-{uuid4().hex}@example.com",
        password_hash="synthetic-integration-hash",
    )
    with factory.begin() as session:
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Tool contract project")
        session.add(project)
        session.flush()
        thread = AgentThread(user_id=user.id, goal_summary="Verify Tool contract")
        session.add(thread)
        session.flush()
        run = AgentRun(
            thread_id=thread.id,
            user_id=user.id,
            prompt_version="study-plan.v1",
        )
        session.add(run)
        session.flush()
    return user, project, run


def _cleanup(
    factory: sessionmaker[Session], *, user_id: UUID, project_id: UUID
) -> None:
    with factory.begin() as session:
        session.execute(
            delete(AgentToolExecution).where(AgentToolExecution.user_id == user_id)
        )
        session.execute(delete(AgentApproval).where(AgentApproval.user_id == user_id))
        session.execute(delete(AgentRun).where(AgentRun.user_id == user_id))
        session.execute(delete(AgentThread).where(AgentThread.user_id == user_id))
        session.execute(delete(Task).where(Task.user_id == user_id))
        session.execute(delete(Project).where(Project.id == project_id))
        session.execute(delete(User).where(User.id == user_id))


def test_database_accepts_exact_four_tool_names_and_rejects_unknown(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    user, project, run = _run_records(factory)
    try:
        with factory.begin() as session:
            for index, tool_name in enumerate(
                (
                    "create_task",
                    "update_task",
                    "batch_create_tasks",
                    "delete_task",
                )
            ):
                session.add(
                    AgentToolExecution(
                        run_id=run.id,
                        user_id=user.id,
                        revision=0,
                        proposal_fingerprint="a" * 64,
                        action_key=f"allowed-{index}",
                        tool_name=tool_name,
                    )
                )

        with factory() as session:
            session.add(
                AgentToolExecution(
                    run_id=run.id,
                    user_id=user.id,
                    revision=0,
                    proposal_fingerprint="b" * 64,
                    action_key="unknown-tool",
                    tool_name="unsafe_tool",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        _cleanup(factory, user_id=user.id, project_id=project.id)


def test_approved_delete_claim_executes_once_and_replays_without_domain_write(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    user, project, run = _run_records(factory)
    fingerprint = "c" * 64
    with factory.begin() as session:
        task = Task(user_id=user.id, project_id=project.id, title="Delete once")
        session.add(task)
        session.add(
            AgentApproval(
                run_id=run.id,
                user_id=user.id,
                revision=0,
                proposal_fingerprint=fingerprint,
                decision=AgentApprovalStatus.APPROVED.value,
                decided_at=run.created_at,
            )
        )
        session.flush()

    action = AgentProposedAction(
        action_key="delete-once",
        tool_name=AgentWriteToolName.DELETE_TASK,
        arguments={"task_id": str(task.id)},
    )
    context = AgentRuntimeContext(user_id=user.id, write_tools_enabled=True)
    calls = 0

    def counted_dispatcher(*args: object, **kwargs: object) -> AgentToolResult:
        nonlocal calls
        calls += 1
        return execute_tool(*args, **kwargs)  # type: ignore[arg-type]

    coordinator = AgentToolExecutionCoordinator(
        run_id=run.id,
        user_id=user.id,
        gateway=AgentDomainGateway(session_factory=factory),
        session_factory=factory,
        dispatcher=counted_dispatcher,
    )
    try:
        first = coordinator(
            action,
            revision=0,
            proposal_fingerprint=fingerprint,
            runtime_context=context,
        )
        replay = coordinator(
            action,
            revision=0,
            proposal_fingerprint=fingerprint,
            runtime_context=context,
        )

        assert isinstance(first, AgentToolMutationResult)
        assert first == replay
        assert first.operation == "delete_task"
        assert first.affected_count == 1
        assert calls == 1
        with factory() as session:
            assert session.get(Task, task.id) is None
            executions = list(
                session.scalars(
                    select(AgentToolExecution).where(
                        AgentToolExecution.run_id == run.id
                    )
                )
            )
            assert len(executions) == 1
            assert executions[0].status == "COMPLETED"
            assert executions[0].tool_name == "delete_task"
    finally:
        _cleanup(factory, user_id=user.id, project_id=project.id)
