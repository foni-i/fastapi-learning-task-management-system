"""Owner-scoped Agent thread and run persistence operations."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_run import (
    AgentApproval,
    AgentApprovalStatus,
    AgentRun,
    AgentRunStatus,
    AgentThread,
)


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

    def create_approval(
        self,
        *,
        approval_id: UUID,
        run_id: UUID,
        user_id: UUID,
        revision: int,
        proposal_fingerprint: str,
    ) -> AgentApproval:
        approval = AgentApproval(
            id=approval_id,
            run_id=run_id,
            user_id=user_id,
            revision=revision,
            proposal_fingerprint=proposal_fingerprint,
            decision=AgentApprovalStatus.PENDING.value,
        )
        self._session.add(approval)
        self._session.flush()
        return approval

    def get_owned_approval(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        revision: int,
    ) -> AgentApproval | None:
        statement = select(AgentApproval).where(
            AgentApproval.run_id == run_id,
            AgentApproval.user_id == user_id,
            AgentApproval.revision == revision,
        )
        return self._session.scalar(statement)

    def get_latest_owned_approval(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
    ) -> AgentApproval | None:
        statement = (
            select(AgentApproval)
            .where(
                AgentApproval.run_id == run_id,
                AgentApproval.user_id == user_id,
            )
            .order_by(AgentApproval.revision.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def list_owned_approvals(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
    ) -> tuple[AgentApproval, ...]:
        """Return the bounded approval history for one owned run."""

        statement = (
            select(AgentApproval)
            .where(
                AgentApproval.run_id == run_id,
                AgentApproval.user_id == user_id,
            )
            .order_by(AgentApproval.revision.asc(), AgentApproval.id.asc())
            .limit(3)
        )
        return tuple(self._session.scalars(statement))

    def update_run(
        self,
        run: AgentRun,
        *,
        values: Mapping[str, object],
        updated_at: datetime,
    ) -> AgentRun:
        for field_name, value in values.items():
            setattr(run, field_name, value)
        run.updated_at = updated_at
        self._session.flush()
        return run

    def update_approval(
        self,
        approval: AgentApproval,
        *,
        decision: str,
        feedback: str | None,
        decided_at: datetime,
    ) -> AgentApproval:
        approval.decision = decision
        approval.feedback = feedback
        approval.decided_at = decided_at
        approval.updated_at = decided_at
        self._session.flush()
        return approval
