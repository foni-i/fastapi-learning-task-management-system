"""Unit contracts for owner-scoped Agent thread and run persistence."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.repositories.agent_runs import AgentRunRepository


def _sql_text(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )


def test_create_thread_uses_trusted_identity_and_only_adds_and_flushes() -> None:
    session = MagicMock(spec=Session)
    repository = AgentRunRepository(session)
    thread_id = uuid4()
    user_id = uuid4()

    thread = repository.create_thread(
        thread_id=thread_id,
        user_id=user_id,
        goal_summary="Build a study plan",
    )

    assert thread.id == thread_id
    assert thread.user_id == user_id
    assert thread.goal_summary == "Build a study plan"
    session.add.assert_called_once_with(thread)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_create_run_binds_thread_owner_and_only_adds_and_flushes() -> None:
    session = MagicMock(spec=Session)
    repository = AgentRunRepository(session)
    run_id = uuid4()
    thread_id = uuid4()
    user_id = uuid4()

    run = repository.create_run(
        run_id=run_id,
        thread_id=thread_id,
        user_id=user_id,
        prompt_version="study-plan.v1",
    )

    assert run.id == run_id
    assert run.thread_id == thread_id
    assert run.user_id == user_id
    assert run.prompt_version == "study-plan.v1"
    session.add.assert_called_once_with(run)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_thread_lookup_includes_identity_and_owner_predicates() -> None:
    session = MagicMock(spec=Session)
    repository = AgentRunRepository(session)

    repository.get_owned_thread(thread_id=uuid4(), user_id=uuid4())

    sql = _sql_text(session.scalar.call_args.args[0])
    assert "agent_threads.id =" in sql
    assert "agent_threads.user_id =" in sql


def test_run_lookup_includes_identity_and_owner_predicates() -> None:
    session = MagicMock(spec=Session)
    repository = AgentRunRepository(session)

    repository.get_owned_run(run_id=uuid4(), user_id=uuid4())

    sql = _sql_text(session.scalar.call_args.args[0])
    assert "agent_runs.id =" in sql
    assert "agent_runs.user_id =" in sql


def test_missing_or_foreign_owned_queries_return_none_without_transaction_work() -> (
    None
):
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    repository = AgentRunRepository(session)

    assert repository.get_owned_thread(thread_id=uuid4(), user_id=uuid4()) is None
    assert repository.get_owned_run(run_id=uuid4(), user_id=uuid4()) is None
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_database_failure_is_not_translated_or_rolled_back() -> None:
    session = MagicMock(spec=Session)
    failure = RuntimeError("synthetic persistence failure")
    session.flush.side_effect = failure
    repository = AgentRunRepository(session)

    try:
        repository.create_thread(
            thread_id=uuid4(),
            user_id=uuid4(),
            goal_summary="Safe goal",
        )
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError("expected persistence failure")

    session.rollback.assert_not_called()
    session.commit.assert_not_called()


def test_repository_source_has_no_checkpoint_or_http_transaction_concerns() -> None:
    source = (
        __import__("pathlib")
        .Path("app/repositories/agent_runs.py")
        .read_text(encoding="utf-8")
    )
    for forbidden in (
        "FastAPI",
        "HTTPException",
        "checkpoint_",
        ".commit(",
        ".rollback(",
        "Authorization",
        "access_token",
    ):
        assert forbidden not in source
