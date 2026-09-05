"""Persistence operations for safe Agent Tool execution records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_tool_execution import (
    AgentToolExecution,
    AgentToolExecutionStatus,
)


class AgentToolExecutionRepository:
    """Mutate execution audit rows through a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_owned_action(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        revision: int,
        proposal_fingerprint: str,
        action_key: str,
    ) -> AgentToolExecution | None:
        statement = select(AgentToolExecution).where(
            AgentToolExecution.run_id == run_id,
            AgentToolExecution.user_id == user_id,
            AgentToolExecution.revision == revision,
            AgentToolExecution.proposal_fingerprint == proposal_fingerprint,
            AgentToolExecution.action_key == action_key,
        )
        return self._session.scalar(statement)

    def create_claim(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        revision: int,
        proposal_fingerprint: str,
        action_key: str,
        tool_name: str,
        started_at: datetime,
    ) -> AgentToolExecution:
        execution = AgentToolExecution(
            run_id=run_id,
            user_id=user_id,
            revision=revision,
            proposal_fingerprint=proposal_fingerprint,
            action_key=action_key,
            tool_name=tool_name,
            status=AgentToolExecutionStatus.IN_PROGRESS.value,
            attempt_count=1,
            started_at=started_at,
            created_at=started_at,
            updated_at=started_at,
        )
        self._session.add(execution)
        self._session.flush()
        return execution

    def restart_failed(
        self, execution: AgentToolExecution, *, started_at: datetime
    ) -> AgentToolExecution:
        execution.status = AgentToolExecutionStatus.IN_PROGRESS.value
        execution.attempt_count += 1
        execution.error_code = None
        execution.completed_at = None
        execution.started_at = started_at
        execution.updated_at = started_at
        self._session.flush()
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
        execution.updated_at = completed_at
        self._session.flush()
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
        execution.updated_at = completed_at
        self._session.flush()
        return execution
