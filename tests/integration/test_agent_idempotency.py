"""Final PostgreSQL proof for approved high-impact idempotent writes."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.agent.context import AgentRuntimeContext
from app.agent.state import AgentProposedAction, AgentWriteToolName
from app.agent.tools import AgentToolResult, execute_tool
from app.core.exceptions import AgentToolReconciliationRequiredError
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

pytestmark = pytest.mark.integration


def test_approved_batch_executes_once_and_replay_is_read_only(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    user = User(
        email=f"agent-final-idempotency-{uuid4().hex}@example.com",
        password_hash="synthetic-integration-hash",
    )
    fingerprint = "f" * 64
    with factory.begin() as session:
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Final idempotency project")
        session.add(project)
        session.flush()
        thread = AgentThread(user_id=user.id, goal_summary="Create a safe batch")
        session.add(thread)
        session.flush()
        run = AgentRun(
            thread_id=thread.id,
            user_id=user.id,
            prompt_version="study-plan.v1",
        )
        session.add(run)
        session.flush()
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

    action = AgentProposedAction(
        action_key="final-batch-once",
        tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
        arguments={
            "tasks": [
                {
                    "project_id": str(project.id),
                    "title": "First durable task",
                    "priority": "MEDIUM",
                },
                {
                    "project_id": str(project.id),
                    "title": "Second durable task",
                    "priority": "HIGH",
                },
            ]
        },
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
        assert first.affected_count == 2
        assert calls == 1

        with pytest.raises(AgentToolReconciliationRequiredError):
            coordinator(
                action,
                revision=0,
                proposal_fingerprint="e" * 64,
                runtime_context=context,
            )
        assert calls == 1

        with factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.user_id == user.id)
                )
                == 2
            )
            executions = tuple(
                session.scalars(
                    select(AgentToolExecution).where(
                        AgentToolExecution.run_id == run.id
                    )
                )
            )
            assert len(executions) == 1
            assert executions[0].status == "COMPLETED"
            assert executions[0].tool_name == "batch_create_tasks"
            assert not hasattr(executions[0], "arguments")
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AgentToolExecution).where(AgentToolExecution.run_id == run.id)
            )
            session.execute(delete(AgentApproval).where(AgentApproval.run_id == run.id))
            session.execute(delete(Task).where(Task.user_id == user.id))
            session.execute(delete(AgentRun).where(AgentRun.id == run.id))
            session.execute(delete(AgentThread).where(AgentThread.id == thread.id))
            session.execute(delete(Project).where(Project.id == project.id))
            session.execute(delete(User).where(User.id == user.id))
