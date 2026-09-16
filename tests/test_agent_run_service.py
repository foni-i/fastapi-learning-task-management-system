"""Unit contracts for Agent thread/run ownership and transactions."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_THREAD_NOT_FOUND_MESSAGE,
    AgentRunNotFoundError,
    AgentThreadNotFoundError,
)
from app.models.agent_run import AgentRun, AgentThread
from app.repositories.agent_recovery import agent_recovery_lock_key
from app.schemas.agent_run import AgentThreadCreate
from app.services.agent_runs import (
    create_agent_run,
    create_agent_thread_with_initial_run,
    get_owned_agent_run,
    get_owned_agent_thread,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


class RecordingSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[object] = []

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1

    def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


class ControlledRepository:
    def __init__(
        self,
        *,
        thread: AgentThread | None = None,
        run: AgentRun | None = None,
        failure_at: str | None = None,
    ) -> None:
        self.thread = thread
        self.run = run
        self.failure_at = failure_at
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def create_thread(
        self, *, thread_id: UUID, user_id: UUID, goal_summary: str
    ) -> AgentThread:
        self.calls.append(("create_thread", (thread_id, user_id, goal_summary)))
        if self.failure_at == "thread":
            raise RuntimeError("synthetic thread failure")
        self.thread = _thread(thread_id=thread_id, user_id=user_id, goal=goal_summary)
        return self.thread

    def create_run(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        prompt_version: str,
    ) -> AgentRun:
        self.calls.append(("create_run", (run_id, thread_id, user_id, prompt_version)))
        if self.failure_at == "run":
            raise RuntimeError("synthetic run failure")
        self.run = _run(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            prompt_version=prompt_version,
        )
        return self.run

    def get_owned_thread(self, *, thread_id: UUID, user_id: UUID) -> AgentThread | None:
        self.calls.append(("get_thread", (thread_id, user_id)))
        return self.thread

    def get_owned_run(self, *, run_id: UUID, user_id: UUID) -> AgentRun | None:
        self.calls.append(("get_run", (run_id, user_id)))
        return self.run


def _thread(*, thread_id: UUID, user_id: UUID, goal: str = "Goal") -> AgentThread:
    return AgentThread(
        id=thread_id,
        user_id=user_id,
        goal_summary=goal,
        status="ACTIVE",
        created_at=NOW,
        updated_at=NOW,
    )


def _run(
    *,
    run_id: UUID,
    thread_id: UUID,
    user_id: UUID,
    prompt_version: str = "study-plan.v1",
) -> AgentRun:
    return AgentRun(
        id=run_id,
        thread_id=thread_id,
        user_id=user_id,
        status="PENDING",
        current_node=None,
        summary=None,
        error_code=None,
        prompt_version=prompt_version,
        model_round_count=0,
        provider_attempt_count=0,
        tool_call_count=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _factory(repository: ControlledRepository) -> Callable[[Session], object]:
    return lambda _session: repository


def _ids(*values: UUID) -> Callable[[], UUID]:
    iterator: Iterator[UUID] = iter(values)
    return lambda: next(iterator)


def test_create_thread_and_initial_run_use_one_transaction_and_trusted_ids() -> None:
    user_id = uuid4()
    thread_id = uuid4()
    run_id = uuid4()
    session = RecordingSession()
    repository = ControlledRepository()

    result = create_agent_thread_with_initial_run(
        AgentThreadCreate(goal_summary="  Learn graphs  "),
        user_id,
        session,  # type: ignore[arg-type]
        repository_factory=_factory(repository),  # type: ignore[arg-type]
        id_factory=_ids(thread_id, run_id),
    )

    assert repository.calls == [
        ("create_thread", (thread_id, user_id, "Learn graphs")),
        ("create_run", (run_id, thread_id, user_id, "study-plan.v2")),
    ]
    assert result.thread.id == thread_id
    assert result.run.id == run_id
    assert result.run.thread_id == thread_id
    assert session.refreshed == [repository.thread, repository.run]
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.parametrize("failure_at", ["thread", "run"])
def test_initial_creation_failure_rolls_back(failure_at: str) -> None:
    session = RecordingSession()
    repository = ControlledRepository(failure_at=failure_at)

    with pytest.raises(RuntimeError):
        create_agent_thread_with_initial_run(
            AgentThreadCreate(goal_summary="Goal"),
            uuid4(),
            session,  # type: ignore[arg-type]
            repository_factory=_factory(repository),  # type: ignore[arg-type]
            id_factory=_ids(uuid4(), uuid4()),
        )

    assert session.commits == 0
    assert session.rollbacks == 1


def test_commit_failure_rolls_back() -> None:
    failure = RuntimeError("synthetic commit failure")
    session = RecordingSession(commit_error=failure)

    with pytest.raises(RuntimeError) as error:
        create_agent_thread_with_initial_run(
            AgentThreadCreate(goal_summary="Goal"),
            uuid4(),
            session,  # type: ignore[arg-type]
            repository_factory=_factory(ControlledRepository()),  # type: ignore[arg-type]
            id_factory=_ids(uuid4(), uuid4()),
        )

    assert error.value is failure
    assert session.commits == 1
    assert session.rollbacks == 1


def test_later_run_first_checks_owned_thread_and_uses_new_run_id() -> None:
    user_id = uuid4()
    thread_id = uuid4()
    run_id = uuid4()
    thread = _thread(thread_id=thread_id, user_id=user_id)
    repository = ControlledRepository(thread=thread)
    session = RecordingSession()

    result = create_agent_run(
        thread_id,
        user_id,
        session,  # type: ignore[arg-type]
        repository_factory=_factory(repository),  # type: ignore[arg-type]
        id_factory=_ids(run_id),
    )

    assert repository.calls == [
        ("get_thread", (thread_id, user_id)),
        ("create_run", (run_id, thread_id, user_id, "study-plan.v2")),
    ]
    assert result.id == run_id
    assert result.thread_id == thread_id
    assert session.refreshed == [repository.run]
    assert session.commits == 1
    assert session.rollbacks == 0


def test_missing_or_foreign_thread_uses_safe_error_without_write() -> None:
    session = RecordingSession()
    repository = ControlledRepository(thread=None)

    with pytest.raises(AgentThreadNotFoundError) as error:
        create_agent_run(
            uuid4(),
            uuid4(),
            session,  # type: ignore[arg-type]
            repository_factory=_factory(repository),  # type: ignore[arg-type]
        )

    assert str(error.value) == AGENT_THREAD_NOT_FOUND_MESSAGE
    assert [name for name, _ in repository.calls] == ["get_thread"]
    assert session.commits == session.rollbacks == 0


def test_later_run_failure_rolls_back() -> None:
    user_id = uuid4()
    thread_id = uuid4()
    repository = ControlledRepository(
        thread=_thread(thread_id=thread_id, user_id=user_id),
        failure_at="run",
    )
    session = RecordingSession()

    with pytest.raises(RuntimeError):
        create_agent_run(
            thread_id,
            user_id,
            session,  # type: ignore[arg-type]
            repository_factory=_factory(repository),  # type: ignore[arg-type]
        )

    assert session.commits == 0
    assert session.rollbacks == 1


def test_owned_reads_return_public_whitelists_without_transaction_changes() -> None:
    user_id = uuid4()
    thread = _thread(thread_id=uuid4(), user_id=user_id)
    run = _run(run_id=uuid4(), thread_id=thread.id, user_id=user_id)
    repository = ControlledRepository(thread=thread, run=run)
    session = RecordingSession()

    public_thread = get_owned_agent_thread(
        thread.id,
        user_id,
        session,  # type: ignore[arg-type]
        repository_factory=_factory(repository),  # type: ignore[arg-type]
    )
    public_run = get_owned_agent_run(
        run.id,
        user_id,
        session,  # type: ignore[arg-type]
        repository_factory=_factory(repository),  # type: ignore[arg-type]
    )

    assert set(public_thread.model_dump()) == {
        "id",
        "goal_summary",
        "status",
        "created_at",
        "updated_at",
    }
    assert "user_id" not in public_run.model_dump_json()
    assert session.commits == session.rollbacks == 0


def test_missing_or_foreign_run_uses_same_safe_error() -> None:
    session = RecordingSession()
    repository = ControlledRepository(run=None)

    with pytest.raises(AgentRunNotFoundError) as error:
        get_owned_agent_run(
            uuid4(),
            uuid4(),
            session,  # type: ignore[arg-type]
            repository_factory=_factory(repository),  # type: ignore[arg-type]
        )

    assert str(error.value) == AGENT_RUN_NOT_FOUND_MESSAGE
    assert session.commits == session.rollbacks == 0


def test_service_source_does_not_call_graph_provider_tools_or_checkpointer() -> None:
    source = (
        __import__("pathlib")
        .Path("app/services/agent_runs.py")
        .read_text(encoding="utf-8")
    )
    for forbidden in (
        "build_agent_graph",
        ".invoke(",
        "open_postgres_checkpointer",
        "ModelProvider",
        "execute_tool",
        "FastAPI",
        "HTTPException",
    ):
        assert forbidden not in source


def test_recovery_lock_key_uses_stable_complete_uuid_mapping() -> None:
    assert (
        agent_recovery_lock_key(UUID("00000000-0000-0000-0000-000000000000"))
        == -7146144241358649407
    )
    assert (
        agent_recovery_lock_key(UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))
        == 8246188560930004617
    )
