"""Focused transaction and replay tests for Agent Tool idempotency."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.context import AgentRuntimeContext
from app.agent.state import AgentProposedAction, AgentWriteToolName
from app.agent.tools import AgentToolGateway
from app.core.exceptions import (
    AgentToolReconciliationRequiredError,
    TaskNotFoundError,
)
from app.models.agent_tool_execution import (
    AgentToolExecution,
    AgentToolExecutionStatus,
)
from app.models.task import TaskPriority, TaskStatus
from app.repositories.agent_tool_executions import AgentToolExecutionRepository
from app.schemas.task import PublicTask
from app.services.agent_tool_executions import AgentToolExecutionCoordinator


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class MemoryRepository(AgentToolExecutionRepository):
    def __init__(
        self,
        session: Session,
        store: dict[tuple[object, ...], AgentToolExecution],
    ) -> None:
        super().__init__(session)
        self.store = store

    @staticmethod
    def _key(
        run_id: UUID,
        revision: int,
        proposal_fingerprint: str,
        action_key: str,
    ) -> tuple[object, ...]:
        return run_id, revision, proposal_fingerprint, action_key

    def get_owned_action(self, **values: object) -> AgentToolExecution | None:
        key = self._key(
            cast(UUID, values["run_id"]),
            cast(int, values["revision"]),
            cast(str, values["proposal_fingerprint"]),
            cast(str, values["action_key"]),
        )
        execution = self.store.get(key)
        if execution is None or execution.user_id != values["user_id"]:
            return None
        return execution

    def create_claim(self, **values: object) -> AgentToolExecution:
        started_at = cast(datetime, values["started_at"])
        execution = AgentToolExecution(
            id=uuid4(),
            run_id=cast(UUID, values["run_id"]),
            user_id=cast(UUID, values["user_id"]),
            revision=cast(int, values["revision"]),
            proposal_fingerprint=cast(str, values["proposal_fingerprint"]),
            action_key=cast(str, values["action_key"]),
            tool_name=cast(str, values["tool_name"]),
            status=AgentToolExecutionStatus.IN_PROGRESS.value,
            attempt_count=1,
            started_at=started_at,
            created_at=started_at,
            updated_at=started_at,
        )
        self.store[
            self._key(
                execution.run_id,
                execution.revision,
                execution.proposal_fingerprint,
                execution.action_key,
            )
        ] = execution
        return execution

    def restart_failed(
        self, execution: AgentToolExecution, *, started_at: datetime
    ) -> AgentToolExecution:
        execution.status = AgentToolExecutionStatus.IN_PROGRESS.value
        execution.attempt_count += 1
        execution.error_code = None
        execution.completed_at = None
        execution.started_at = started_at
        return execution

    def complete(
        self,
        execution: AgentToolExecution,
        *,
        task_id: UUID,
        summary: str,
        completed_at: datetime,
    ) -> AgentToolExecution:
        execution.status = AgentToolExecutionStatus.COMPLETED.value
        execution.result_task_id = task_id
        execution.result_summary = summary
        execution.completed_at = completed_at
        return execution

    def fail(
        self,
        execution: AgentToolExecution,
        *,
        status: AgentToolExecutionStatus,
        error_code: str,
        completed_at: datetime,
    ) -> AgentToolExecution:
        execution.status = status.value
        execution.error_code = error_code
        execution.completed_at = completed_at
        return execution


def _task(task_id: UUID | None = None) -> PublicTask:
    now = datetime.now(UTC)
    return PublicTask(
        id=task_id or uuid4(),
        project_id=uuid4(),
        title="Safe result",
        description=None,
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


def _action() -> AgentProposedAction:
    return AgentProposedAction(
        action_key="create-first",
        tool_name=AgentWriteToolName.CREATE_TASK,
        arguments={"title": "not persisted by audit"},
    )


def _coordinator(
    *,
    run_id: UUID,
    user_id: UUID,
    store: dict[tuple[object, ...], AgentToolExecution],
    sessions: list[FakeSession],
    dispatcher: Callable[..., PublicTask],
    loaded: PublicTask,
) -> AgentToolExecutionCoordinator:
    def session_factory() -> Session:
        session = FakeSession()
        sessions.append(session)
        return cast(Session, session)

    def repository_factory(session: Session) -> AgentToolExecutionRepository:
        return MemoryRepository(session, store)

    def task_loader(task_id: UUID, owner_id: UUID, session: Session) -> PublicTask:
        assert task_id == loaded.id
        assert owner_id == user_id
        return loaded

    return AgentToolExecutionCoordinator(
        run_id=run_id,
        user_id=user_id,
        gateway=cast(AgentToolGateway, object()),
        session_factory=session_factory,
        repository_factory=repository_factory,
        dispatcher=dispatcher,
        task_loader=task_loader,
    )


def test_first_execution_is_claimed_and_completed_then_replayed_without_tool() -> None:
    run_id = uuid4()
    user_id = uuid4()
    store: dict[tuple[object, ...], AgentToolExecution] = {}
    sessions: list[FakeSession] = []
    calls = 0
    result = _task()

    def dispatcher(*args: object, **kwargs: object) -> PublicTask:
        nonlocal calls
        calls += 1
        return result

    coordinator = _coordinator(
        run_id=run_id,
        user_id=user_id,
        store=store,
        sessions=sessions,
        dispatcher=dispatcher,
        loaded=result,
    )
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)

    first = coordinator(
        _action(), revision=1, proposal_fingerprint="a" * 64, runtime_context=context
    )
    replay = coordinator(
        _action(), revision=1, proposal_fingerprint="a" * 64, runtime_context=context
    )

    assert first == replay == result
    assert calls == 1
    execution = next(iter(store.values()))
    assert execution.status == "COMPLETED"
    assert execution.result_task_id == result.id
    assert execution.result_summary == result.title
    assert not hasattr(execution, "arguments")
    assert all(session.closed for session in sessions)
    assert all(session.rollbacks == 0 for session in sessions)


def test_proven_failure_is_retryable_but_unknown_outcome_fails_closed() -> None:
    run_id = uuid4()
    user_id = uuid4()
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    result = _task()

    safe_store: dict[tuple[object, ...], AgentToolExecution] = {}
    safe_sessions: list[FakeSession] = []
    safe_calls = 0

    def retrying_dispatcher(*args: object, **kwargs: object) -> PublicTask:
        nonlocal safe_calls
        safe_calls += 1
        if safe_calls == 1:
            raise TaskNotFoundError("safe")
        return result

    retrying = _coordinator(
        run_id=run_id,
        user_id=user_id,
        store=safe_store,
        sessions=safe_sessions,
        dispatcher=retrying_dispatcher,
        loaded=result,
    )
    with pytest.raises(TaskNotFoundError):
        retrying(
            _action(),
            revision=1,
            proposal_fingerprint="b" * 64,
            runtime_context=context,
        )
    assert next(iter(safe_store.values())).status == "FAILED"
    assert (
        retrying(
            _action(),
            revision=1,
            proposal_fingerprint="b" * 64,
            runtime_context=context,
        )
        == result
    )
    assert safe_calls == 2
    assert next(iter(safe_store.values())).attempt_count == 2

    unknown_store: dict[tuple[object, ...], AgentToolExecution] = {}
    unknown_sessions: list[FakeSession] = []
    unknown_calls = 0

    def unknown_dispatcher(*args: object, **kwargs: object) -> PublicTask:
        nonlocal unknown_calls
        unknown_calls += 1
        raise RuntimeError("private diagnostic")

    unknown = _coordinator(
        run_id=run_id,
        user_id=user_id,
        store=unknown_store,
        sessions=unknown_sessions,
        dispatcher=unknown_dispatcher,
        loaded=result,
    )
    with pytest.raises(RuntimeError):
        unknown(
            _action(),
            revision=1,
            proposal_fingerprint="c" * 64,
            runtime_context=context,
        )
    with pytest.raises(AgentToolReconciliationRequiredError) as error:
        unknown(
            _action(),
            revision=1,
            proposal_fingerprint="c" * 64,
            runtime_context=context,
        )
    assert str(error.value) == "Agent action requires reconciliation"
    assert "private diagnostic" not in str(error.value)
    assert unknown_calls == 1
    assert next(iter(unknown_store.values())).status == "UNKNOWN"


def test_trusted_owner_mismatch_fails_before_opening_a_session() -> None:
    sessions: list[FakeSession] = []
    coordinator = _coordinator(
        run_id=uuid4(),
        user_id=uuid4(),
        store={},
        sessions=sessions,
        dispatcher=lambda *args, **kwargs: _task(),
        loaded=_task(),
    )
    with pytest.raises(AgentToolReconciliationRequiredError):
        coordinator(
            _action(),
            revision=0,
            proposal_fingerprint="d" * 64,
            runtime_context=AgentRuntimeContext(
                user_id=uuid4(), write_tools_enabled=True
            ),
        )
    assert sessions == []


def test_failure_recording_after_domain_success_marks_unknown_and_never_retries() -> (
    None
):
    run_id = uuid4()
    user_id = uuid4()
    store: dict[tuple[object, ...], AgentToolExecution] = {}
    sessions: list[FakeSession] = []
    result = _task()
    tool_calls = 0
    completion_attempted = False

    class FailingCompletionRepository(MemoryRepository):
        def complete(
            self,
            execution: AgentToolExecution,
            *,
            task_id: UUID,
            summary: str,
            completed_at: datetime,
        ) -> AgentToolExecution:
            nonlocal completion_attempted
            if not completion_attempted:
                completion_attempted = True
                raise RuntimeError("synthetic audit write failure")
            return super().complete(
                execution,
                task_id=task_id,
                summary=summary,
                completed_at=completed_at,
            )

    def session_factory() -> Session:
        session = FakeSession()
        sessions.append(session)
        return cast(Session, session)

    def repository_factory(session: Session) -> AgentToolExecutionRepository:
        return FailingCompletionRepository(session, store)

    def dispatcher(*args: object, **kwargs: object) -> PublicTask:
        nonlocal tool_calls
        tool_calls += 1
        return result

    coordinator = AgentToolExecutionCoordinator(
        run_id=run_id,
        user_id=user_id,
        gateway=cast(AgentToolGateway, object()),
        session_factory=session_factory,
        repository_factory=repository_factory,
        dispatcher=dispatcher,
        task_loader=lambda task_id, owner_id, session: result,
    )
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    with pytest.raises(AgentToolReconciliationRequiredError):
        coordinator(
            _action(),
            revision=1,
            proposal_fingerprint="f" * 64,
            runtime_context=context,
        )
    execution = next(iter(store.values()))
    assert execution.status == "UNKNOWN"
    assert execution.error_code == "AGENT_ACTION_OUTCOME_UNKNOWN"

    with pytest.raises(AgentToolReconciliationRequiredError):
        coordinator(
            _action(),
            revision=1,
            proposal_fingerprint="f" * 64,
            runtime_context=context,
        )
    assert tool_calls == 1
    assert all(session.closed for session in sessions)
