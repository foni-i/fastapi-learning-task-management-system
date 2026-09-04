"""Owner-scoped Agent thread and run persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun, AgentRunStatus, AgentThread


class AgentRunRepository:
    """Persist product Agent identities through a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_thread(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        goal_summary: str,
    ) -> AgentThread:
        """Add one host-identified, owner-derived thread and flush it."""

        thread = AgentThread(
            id=thread_id,
            user_id=user_id,
            goal_summary=goal_summary,
        )
        self._session.add(thread)
        self._session.flush()
        return thread

    def create_run(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        prompt_version: str,
    ) -> AgentRun:
        """Add one pending run beneath an owner-validated thread and flush it."""

        run = AgentRun(
            id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            status=AgentRunStatus.PENDING.value,
            prompt_version=prompt_version,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get_owned_thread(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
    ) -> AgentThread | None:
        """Find one thread only when both identity and owner match."""

        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.user_id == user_id,
        )
        return self._session.scalar(statement)

    def get_owned_run(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
    ) -> AgentRun | None:
        """Find one run only when both identity and owner match."""

        statement = select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
        )
        return self._session.scalar(statement)
