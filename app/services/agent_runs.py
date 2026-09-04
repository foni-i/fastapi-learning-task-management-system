"""Owner-safe Agent thread/run use cases and transaction boundaries."""

from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION
from app.core.exceptions import (
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_THREAD_NOT_FOUND_MESSAGE,
    AgentRunNotFoundError,
    AgentThreadNotFoundError,
)
from app.models.agent_run import AgentRun, AgentThread
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentThreadCreate,
    AgentThreadRunResult,
    PublicAgentRun,
    PublicAgentThread,
)

RepositoryFactory = Callable[[Session], AgentRunRepository]
IdFactory = Callable[[], UUID]


def _require_owned_thread(thread: AgentThread | None) -> AgentThread:
    if thread is None:
        raise AgentThreadNotFoundError(AGENT_THREAD_NOT_FOUND_MESSAGE)
    return thread


def _require_owned_run(run: AgentRun | None) -> AgentRun:
    if run is None:
        raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    return run


def create_agent_thread_with_initial_run(
    thread_input: AgentThreadCreate,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
    id_factory: IdFactory = uuid4,
    prompt_version: str = STUDY_PLAN_PROMPT_VERSION,
) -> AgentThreadRunResult:
    """Create one thread and its initial run in one owned transaction."""

    repository = repository_factory(session)
    try:
        thread = repository.create_thread(
            thread_id=id_factory(),
            user_id=user_id,
            goal_summary=thread_input.goal_summary,
        )
        run = repository.create_run(
            run_id=id_factory(),
            thread_id=thread.id,
            user_id=user_id,
            prompt_version=prompt_version,
        )
        session.refresh(thread)
        session.refresh(run)
        result = AgentThreadRunResult(
            thread=PublicAgentThread.model_validate(thread),
            run=PublicAgentRun.model_validate(run),
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def create_agent_run(
    thread_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
    id_factory: IdFactory = uuid4,
    prompt_version: str = STUDY_PLAN_PROMPT_VERSION,
) -> PublicAgentRun:
    """Create a later run only beneath a thread owned by the trusted user."""

    repository = repository_factory(session)
    thread = _require_owned_thread(
        repository.get_owned_thread(thread_id=thread_id, user_id=user_id)
    )
    try:
        run = repository.create_run(
            run_id=id_factory(),
            thread_id=thread.id,
            user_id=user_id,
            prompt_version=prompt_version,
        )
        session.refresh(run)
        result = PublicAgentRun.model_validate(run)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def get_owned_agent_thread(
    thread_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
) -> PublicAgentThread:
    """Read one owner-scoped product thread without transaction mutation."""

    thread = repository_factory(session).get_owned_thread(
        thread_id=thread_id,
        user_id=user_id,
    )
    return PublicAgentThread.model_validate(_require_owned_thread(thread))


def get_owned_agent_run(
    run_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
) -> PublicAgentRun:
    """Read one owner-scoped product run without transaction mutation."""

    run = repository_factory(session).get_owned_run(run_id=run_id, user_id=user_id)
    return PublicAgentRun.model_validate(_require_owned_run(run))
