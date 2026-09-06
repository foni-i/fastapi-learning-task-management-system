"""Product audit and SSE remain separate from Checkpoint persistence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AgentApproval,
    AgentRun,
    AgentThread,
    AgentToolExecution,
    User,
)
from app.models.agent_run import AgentApprovalStatus, AgentRunStatus
from app.models.agent_tool_execution import AgentToolExecutionStatus
from app.services.agent_events import get_owned_agent_events

pytestmark = pytest.mark.integration


def test_product_audit_is_bounded_safe_and_checkpoint_independent(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    user = User(
        email=f"agent-final-audit-{uuid4().hex}@example.com",
        password_hash="synthetic-integration-hash",
    )
    with factory.begin() as session:
        session.add(user)
        session.flush()
        thread = AgentThread(user_id=user.id, goal_summary="Audit safely")
        session.add(thread)
        session.flush()
        run = AgentRun(
            thread_id=thread.id,
            user_id=user.id,
            status=AgentRunStatus.SUCCEEDED.value,
            current_node="summarize",
            summary="Safe final summary",
            prompt_version="study-plan.v1",
            model_round_count=1,
            provider_attempt_count=1,
            tool_call_count=1,
            updated_at=now + timedelta(seconds=3),
        )
        session.add(run)
        session.flush()
        session.add(
            AgentApproval(
                run_id=run.id,
                user_id=user.id,
                revision=0,
                proposal_fingerprint="a" * 64,
                decision=AgentApprovalStatus.APPROVED.value,
                decided_at=now + timedelta(seconds=1),
            )
        )
        session.add(
            AgentToolExecution(
                run_id=run.id,
                user_id=user.id,
                revision=0,
                proposal_fingerprint="a" * 64,
                action_key="audit-once",
                tool_name="delete_task",
                status=AgentToolExecutionStatus.COMPLETED.value,
                result_task_id=uuid4(),
                result_summary="Task deleted",
                started_at=now + timedelta(seconds=1),
                completed_at=now + timedelta(seconds=2),
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=2),
            )
        )

    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.casefold())

    event.listen(integration_engine, "before_cursor_execute", record_statement)
    try:
        with factory() as session:
            events = get_owned_agent_events(run.id, user.id, session)
        types = [item.event_type.value for item in events]
        assert types.count("terminal_result") == 1
        assert "tool_started" in types
        assert "tool_result" in types
        assert "metrics" in types
        assert len(events) <= 2 * 50 + 4
        serialized = "".join(item.model_dump_json() for item in events).casefold()
        for forbidden in (
            "user_id",
            "checkpoint",
            "hidden_reasoning",
            "arguments",
            "authorization",
            "database_url",
            "synthetic-integration-hash",
        ):
            assert forbidden not in serialized
        assert statements
        assert all("checkpoint" not in statement for statement in statements)
        assert any("agent_runs" in statement for statement in statements)
        assert any("agent_approvals" in statement for statement in statements)
        assert any("agent_tool_executions" in statement for statement in statements)
    finally:
        event.remove(integration_engine, "before_cursor_execute", record_statement)
        with factory.begin() as session:
            session.execute(
                delete(AgentToolExecution).where(AgentToolExecution.run_id == run.id)
            )
            session.execute(delete(AgentApproval).where(AgentApproval.run_id == run.id))
            session.execute(delete(AgentRun).where(AgentRun.id == run.id))
            session.execute(delete(AgentThread).where(AgentThread.id == thread.id))
            session.execute(delete(User).where(User.id == user.id))
