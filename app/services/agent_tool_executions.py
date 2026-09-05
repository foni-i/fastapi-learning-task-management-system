"""Coordinate durable at-most-once intent around approved Agent Tool writes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Never
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.context import AgentRuntimeContext
from app.agent.policy import is_high_impact_tool
from app.agent.state import AgentProposedAction
from app.agent.tools import (
    AgentToolGateway,
    AgentToolResult,
    AgentWriteToolResult,
    execute_tool,
    validate_write_tool_result,
)
from app.core.exceptions import (
    AGENT_TOOL_RECONCILIATION_MESSAGE,
    AgentToolReconciliationRequiredError,
    ArchivedProjectError,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.db.session import get_session_factory
from app.models.agent_run import AgentApprovalStatus
from app.models.agent_tool_execution import (
    AgentToolExecution,
    AgentToolExecutionStatus,
)
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.agent_tool_executions import AgentToolExecutionRepository
from app.schemas.agent_tool import AgentToolMutationResult
from app.schemas.task import PublicTask
from app.services.tasks import get_owned_task

AGENT_TOOL_EXECUTION_UNIQUE_CONSTRAINT = "uq_agent_tool_executions_action_identity"
AGENT_TOOL_EXECUTION_FAILED = "AGENT_ACTION_FAILED"
AGENT_TOOL_EXECUTION_UNKNOWN = "AGENT_ACTION_OUTCOME_UNKNOWN"

SessionFactory = Callable[[], Session]
RepositoryFactory = Callable[[Session], AgentToolExecutionRepository]
ApprovalRepositoryFactory = Callable[[Session], AgentRunRepository]
Clock = Callable[[], datetime]
ToolDispatcher = Callable[..., AgentToolResult]
TaskLoader = Callable[[UUID, UUID, Session], PublicTask]

_PROVEN_NO_WRITE_ERRORS = (
    TaskNotFoundError,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskTransitionError,
    ArchivedProjectError,
)


class _ClaimDisposition(StrEnum):
    EXECUTE = "EXECUTE"
    REPLAY = "REPLAY"


@dataclass(frozen=True)
class _ExecutionIdentity:
    run_id: UUID
    user_id: UUID
    revision: int
    proposal_fingerprint: str
    action_key: str


@dataclass(frozen=True)
class _ReplayData:
    tool_name: str
    result_task_id: UUID
    result_summary: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def _new_session() -> Session:
    return get_session_factory()()


def _is_duplicate_claim(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return (
        getattr(diagnostic, "constraint_name", None)
        == AGENT_TOOL_EXECUTION_UNIQUE_CONSTRAINT
    )


class AgentToolExecutionCoordinator:
    """Persist intent before one domain write and reconstruct safe replays."""

    def __init__(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        gateway: AgentToolGateway,
        session_factory: SessionFactory = _new_session,
        repository_factory: RepositoryFactory = AgentToolExecutionRepository,
        approval_repository_factory: ApprovalRepositoryFactory = AgentRunRepository,
        dispatcher: ToolDispatcher = execute_tool,
        task_loader: TaskLoader = get_owned_task,
        clock: Clock = utc_now,
    ) -> None:
        self._run_id = run_id
        self._user_id = user_id
        self._gateway = gateway
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._approval_repository_factory = approval_repository_factory
        self._dispatcher = dispatcher
        self._task_loader = task_loader
        self._clock = clock

    def __call__(
        self,
        action: AgentProposedAction,
        *,
        revision: int,
        proposal_fingerprint: str,
        runtime_context: AgentRuntimeContext,
    ) -> AgentWriteToolResult:
        if (
            runtime_context.user_id != self._user_id
            or not runtime_context.write_tools_enabled
        ):
            self._fail_closed()

        disposition, replay_data = self._claim(
            action=action,
            revision=revision,
            proposal_fingerprint=proposal_fingerprint,
        )
        if disposition is _ClaimDisposition.REPLAY:
            assert replay_data is not None
            return self._load_public_result(replay_data)

        try:
            result = self._dispatcher(
                action.tool_name.value,
                action.arguments,
                runtime_context,
                gateway=self._gateway,
            )
            public_result = validate_write_tool_result(action.tool_name.value, result)
        except Exception as exc:
            status = (
                AgentToolExecutionStatus.FAILED
                if isinstance(exc, _PROVEN_NO_WRITE_ERRORS)
                else AgentToolExecutionStatus.UNKNOWN
            )
            code = (
                AGENT_TOOL_EXECUTION_FAILED
                if status is AgentToolExecutionStatus.FAILED
                else AGENT_TOOL_EXECUTION_UNKNOWN
            )
            self._record_failure(
                action=action,
                revision=revision,
                proposal_fingerprint=proposal_fingerprint,
                status=status,
                error_code=code,
            )
            raise

        try:
            self._record_completion(
                action=action,
                revision=revision,
                proposal_fingerprint=proposal_fingerprint,
                result=public_result,
            )
        except Exception:
            self._record_failure(
                action=action,
                revision=revision,
                proposal_fingerprint=proposal_fingerprint,
                status=AgentToolExecutionStatus.UNKNOWN,
                error_code=AGENT_TOOL_EXECUTION_UNKNOWN,
            )
            self._fail_closed()
        return public_result

    def _identity(
        self, action: AgentProposedAction, revision: int, fingerprint: str
    ) -> _ExecutionIdentity:
        return _ExecutionIdentity(
            run_id=self._run_id,
            user_id=self._user_id,
            revision=revision,
            proposal_fingerprint=fingerprint,
            action_key=action.action_key,
        )

    @staticmethod
    def _get_owned_action(
        repository: AgentToolExecutionRepository,
        identity: _ExecutionIdentity,
    ) -> AgentToolExecution | None:
        return repository.get_owned_action(
            run_id=identity.run_id,
            user_id=identity.user_id,
            revision=identity.revision,
            proposal_fingerprint=identity.proposal_fingerprint,
            action_key=identity.action_key,
        )

    def _classify_existing(
        self,
        execution: AgentToolExecution,
        repository: AgentToolExecutionRepository,
    ) -> tuple[_ClaimDisposition, _ReplayData | None]:
        status = AgentToolExecutionStatus(execution.status)
        if status is AgentToolExecutionStatus.COMPLETED:
            if execution.result_task_id is None or execution.result_summary is None:
                self._fail_closed()
            return _ClaimDisposition.REPLAY, _ReplayData(
                tool_name=execution.tool_name,
                result_task_id=execution.result_task_id,
                result_summary=execution.result_summary,
            )
        if status is AgentToolExecutionStatus.FAILED:
            repository.restart_failed(execution, started_at=self._clock())
            return _ClaimDisposition.EXECUTE, None
        self._fail_closed()

    def _claim(
        self,
        *,
        action: AgentProposedAction,
        revision: int,
        proposal_fingerprint: str,
    ) -> tuple[_ClaimDisposition, _ReplayData | None]:
        identity = self._identity(action, revision, proposal_fingerprint)
        session = self._session_factory()
        try:
            repository = self._repository_factory(session)
            self._require_persisted_approval(
                session,
                action=action,
                revision=revision,
                proposal_fingerprint=proposal_fingerprint,
            )
            existing = self._get_owned_action(repository, identity)
            if existing is None:
                repository.create_claim(
                    run_id=identity.run_id,
                    user_id=identity.user_id,
                    revision=identity.revision,
                    proposal_fingerprint=identity.proposal_fingerprint,
                    action_key=identity.action_key,
                    tool_name=action.tool_name.value,
                    started_at=self._clock(),
                )
                disposition: tuple[_ClaimDisposition, _ReplayData | None] = (
                    _ClaimDisposition.EXECUTE,
                    None,
                )
            else:
                if existing.tool_name != action.tool_name.value:
                    self._fail_closed()
                disposition = self._classify_existing(existing, repository)
            session.commit()
            return disposition
        except IntegrityError as error:
            session.rollback()
            if not _is_duplicate_claim(error):
                raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        session = self._session_factory()
        try:
            repository = self._repository_factory(session)
            existing = self._get_owned_action(repository, identity)
            if existing is None or existing.tool_name != action.tool_name.value:
                self._fail_closed()
            disposition = self._classify_existing(existing, repository)
            session.commit()
            return disposition
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_completion(
        self,
        *,
        action: AgentProposedAction,
        revision: int,
        proposal_fingerprint: str,
        result: AgentWriteToolResult,
    ) -> None:
        session = self._session_factory()
        try:
            repository = self._repository_factory(session)
            execution = self._get_owned_action(
                repository,
                self._identity(action, revision, proposal_fingerprint),
            )
            if (
                execution is None
                or execution.status != AgentToolExecutionStatus.IN_PROGRESS.value
            ):
                self._fail_closed()
            if isinstance(result, PublicTask):
                task_id = result.id
                summary = result.title
            else:
                task_id = result.reference_task_id
                summary = result.summary
            repository.complete(
                execution,
                task_id=task_id,
                summary=summary,
                completed_at=self._clock(),
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_failure(
        self,
        *,
        action: AgentProposedAction,
        revision: int,
        proposal_fingerprint: str,
        status: AgentToolExecutionStatus,
        error_code: str,
    ) -> None:
        session = self._session_factory()
        try:
            repository = self._repository_factory(session)
            execution = self._get_owned_action(
                repository,
                self._identity(action, revision, proposal_fingerprint),
            )
            if execution is None:
                self._fail_closed()
            if execution.status == AgentToolExecutionStatus.COMPLETED.value:
                self._fail_closed()
            repository.fail(
                execution,
                status=status,
                error_code=error_code,
                completed_at=self._clock(),
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _load_public_result(self, replay: _ReplayData) -> AgentWriteToolResult:
        if replay.tool_name == "delete_task":
            return AgentToolMutationResult(
                operation="delete_task",
                reference_task_id=replay.result_task_id,
                affected_count=1,
                summary=replay.result_summary,
            )
        if replay.tool_name == "batch_create_tasks":
            parts = replay.result_summary.split()
            if len(parts) != 3 or parts[0] != "Created" or parts[2] != "tasks":
                self._fail_closed()
            try:
                affected_count = int(parts[1])
            except ValueError:
                self._fail_closed()
            return AgentToolMutationResult(
                operation="batch_create_tasks",
                reference_task_id=replay.result_task_id,
                affected_count=affected_count,
                summary=replay.result_summary,
            )
        session = self._session_factory()
        try:
            return self._task_loader(replay.result_task_id, self._user_id, session)
        except Exception:
            self._fail_closed()
        finally:
            session.close()

    def _require_persisted_approval(
        self,
        session: Session,
        *,
        action: AgentProposedAction,
        revision: int,
        proposal_fingerprint: str,
    ) -> None:
        if not is_high_impact_tool(action.tool_name.value):
            return
        approval = self._approval_repository_factory(session).get_owned_approval(
            run_id=self._run_id,
            user_id=self._user_id,
            revision=revision,
        )
        if (
            approval is None
            or approval.decision != AgentApprovalStatus.APPROVED.value
            or approval.proposal_fingerprint != proposal_fingerprint
        ):
            self._fail_closed()

    @staticmethod
    def _fail_closed() -> Never:
        raise AgentToolReconciliationRequiredError(AGENT_TOOL_RECONCILIATION_MESSAGE)
