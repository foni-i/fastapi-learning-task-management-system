"""Durable Agent run orchestration across product records and checkpoints."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from time import monotonic, sleep
from typing import Never
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.checkpointing import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.graph import (
    AgentCheckpointStatus,
    AgentWorkflow,
    AgentWorkflowProgress,
    build_agent_graph,
)
from app.agent.nodes.approval import (
    AgentApprovalRequest,
    AgentApprovalResponse,
    ApprovalDecider,
)
from app.agent.providers import OpenAIProvider
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION
from app.agent.state import AgentApprovalDecision, AgentGraphInput, AgentTerminalStatus
from app.agent.tracing import NOOP_TRACE_SINK, TraceSink
from app.core.config import get_settings
from app.core.exceptions import (
    AGENT_RUN_CONFLICT_MESSAGE,
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentWorkflowUnavailableError,
)
from app.models.agent_run import (
    AgentApproval,
    AgentApprovalStatus,
    AgentRun,
    AgentRunStatus,
)
from app.repositories.agent_recovery import (
    AgentRecoveryLockError,
    open_agent_recovery_lock,
)
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentApprovalPreview,
    AgentApprovalSubmission,
    AgentRunSnapshot,
    AgentRunStartRequest,
    PublicAgentApproval,
    PublicAgentRun,
    PublicAgentThread,
)
from app.services.agent_domain import AgentDomainGateway
from app.services.agent_tool_executions import AgentToolExecutionCoordinator

RepositoryFactory = Callable[[Session], AgentRunRepository]
IdFactory = Callable[[], UUID]
Clock = Callable[[], datetime]
WorkflowFactory = Callable[[UUID], AbstractContextManager[AgentWorkflow]]
RecoveryLockFactory = Callable[[Session, UUID], AbstractContextManager[None]]
FaultHook = Callable[[], None]

_TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED.value,
    AgentRunStatus.REJECTED.value,
    AgentRunStatus.PARTIAL_FAILURE.value,
    AgentRunStatus.FAILED.value,
}


class _DurableApprovalOnly(ApprovalDecider):
    def decide(self, request: AgentApprovalRequest) -> Never:
        raise RuntimeError("Durable workflow must use LangGraph interrupt")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _no_fault() -> None:
    return None


@contextmanager
def open_runtime_workflow(
    user_id: UUID,
    *,
    run_id: UUID | None = None,
    trace_sink: TraceSink = NOOP_TRACE_SINK,
) -> Iterator[AgentWorkflow]:
    """Build one production workflow without exposing credentials or connections."""

    settings = get_settings()
    if (
        settings.model_provider != "openai"
        or settings.model_name is None
        or settings.model_api_key is None
    ):
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)
    try:
        if run_id is None:
            raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)
        with open_postgres_checkpointer() as checkpointer:
            gateway = AgentDomainGateway()
            yield build_agent_graph(
                model=settings.model_name,
                provider=OpenAIProvider(api_key=settings.model_api_key),
                gateway=gateway,
                runtime_context=AgentRuntimeContext(
                    user_id=user_id,
                    write_tools_enabled=True,
                ),
                approval_decider=_DurableApprovalOnly(),
                clock=monotonic,
                sleeper=sleep,
                checkpointer=checkpointer,
                durable_approval=True,
                action_executor=AgentToolExecutionCoordinator(
                    run_id=run_id,
                    user_id=user_id,
                    gateway=gateway,
                    trace_sink=trace_sink,
                ),
                trace_sink=trace_sink,
                trace_run_id=run_id,
            )
    except AgentWorkflowUnavailableError:
        raise
    except Exception:
        raise AgentWorkflowUnavailableError(
            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
        ) from None


def _open_workflow(
    workflow_factory: WorkflowFactory,
    *,
    user_id: UUID,
    run_id: UUID,
    trace_sink: TraceSink,
) -> AbstractContextManager[AgentWorkflow]:
    """Pass trusted run identity only to the production durable factory."""

    if workflow_factory is open_runtime_workflow:
        return open_runtime_workflow(
            user_id,
            run_id=run_id,
            trace_sink=trace_sink,
        )
    return workflow_factory(user_id)


def _snapshot(
    repository: AgentRunRepository,
    *,
    run_id: UUID,
    user_id: UUID,
) -> AgentRunSnapshot:
    run = repository.get_owned_run(run_id=run_id, user_id=user_id)
    if run is None:
        raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    thread = repository.get_owned_thread(thread_id=run.thread_id, user_id=user_id)
    if thread is None:
        raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    approval = repository.get_latest_owned_approval(run_id=run.id, user_id=user_id)
    return AgentRunSnapshot(
        thread=PublicAgentThread.model_validate(thread),
        run=PublicAgentRun.model_validate(run),
        approval=(
            None if approval is None else PublicAgentApproval.model_validate(approval)
        ),
    )


def _run_values(progress: AgentWorkflowProgress) -> dict[str, object]:
    if progress.approval is not None:
        return {
            "status": AgentRunStatus.PENDING_APPROVAL.value,
            "current_node": "request_approval",
        }
    if progress.output is None:
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)
    status = {
        AgentTerminalStatus.SUCCEEDED: AgentRunStatus.SUCCEEDED,
        AgentTerminalStatus.REJECTED: AgentRunStatus.REJECTED,
        AgentTerminalStatus.PARTIAL_FAILURE: AgentRunStatus.PARTIAL_FAILURE,
        AgentTerminalStatus.FAILED: AgentRunStatus.FAILED,
    }[progress.output.status]
    values: dict[str, object] = {
        "status": status.value,
        "current_node": "summarize",
        "summary": progress.output.summary,
    }
    if progress.output.metrics is not None:
        raw_metrics = progress.output.metrics.model_dump()
        metrics = {
            counter_name: raw_metrics[counter_name]
            for counter_name in (
                "model_round_count",
                "provider_attempt_count",
                "tool_call_count",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "latency_ms",
            )
        }
        for counter_name in ("input_tokens", "output_tokens", "total_tokens"):
            if metrics[counter_name] is None:
                metrics[counter_name] = 0
        values.update(metrics)
    return values


def start_agent_run(
    request: AgentRunStartRequest,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
    workflow_factory: WorkflowFactory = open_runtime_workflow,
    id_factory: IdFactory = uuid4,
    clock: Clock = utc_now,
    trace_sink: TraceSink = NOOP_TRACE_SINK,
) -> AgentRunSnapshot:
    """Create product identities and run durably to the first approval boundary."""

    repository = repository_factory(session)
    try:
        thread = repository.create_thread(
            thread_id=id_factory(),
            user_id=user_id,
            goal_summary=request.goal.objective,
        )
        run = repository.create_run(
            run_id=id_factory(),
            thread_id=thread.id,
            user_id=user_id,
            prompt_version=STUDY_PLAN_PROMPT_VERSION,
        )
        with _open_workflow(
            workflow_factory,
            user_id=user_id,
            run_id=run.id,
            trace_sink=trace_sink,
        ) as workflow:
            progress = workflow.start_durable(
                AgentGraphInput(goal=request.goal),
                thread_id=thread.id,
            )
        repository.update_run(run, values=_run_values(progress), updated_at=clock())
        if progress.approval is not None:
            repository.create_approval(
                approval_id=id_factory(),
                run_id=run.id,
                user_id=user_id,
                revision=progress.approval.revision,
                proposal_fingerprint=progress.approval.proposal_fingerprint,
            )
        session.commit()
        return _snapshot(repository, run_id=run.id, user_id=user_id)
    except Exception:
        session.rollback()
        raise


def get_agent_run_snapshot(
    run_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
) -> AgentRunSnapshot:
    """Read one owner-scoped safe snapshot without transaction mutation."""

    return _snapshot(repository_factory(session), run_id=run_id, user_id=user_id)


def get_agent_approval_preview(
    run_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
    workflow_factory: WorkflowFactory = open_runtime_workflow,
    trace_sink: TraceSink = NOOP_TRACE_SINK,
    recovery_lock_factory: RecoveryLockFactory = open_agent_recovery_lock,
) -> AgentApprovalPreview:
    """Read the exact pending proposal under the durable recovery lock."""

    try:
        repository = repository_factory(session)
        owned_run = repository.get_owned_run(run_id=run_id, user_id=user_id)
        if owned_run is None:
            raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
        owned_thread = repository.get_owned_thread(
            thread_id=owned_run.thread_id,
            user_id=user_id,
        )
        if owned_thread is None:
            raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)

        with recovery_lock_factory(session, run_id):
            session.expire_all()
            run = repository.get_owned_run(run_id=run_id, user_id=user_id)
            if run is None:
                raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
            thread = repository.get_owned_thread(
                thread_id=run.thread_id,
                user_id=user_id,
            )
            approval = repository.get_latest_owned_approval(
                run_id=run_id,
                user_id=user_id,
            )
            if thread is None:
                raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
            if (
                run.status != AgentRunStatus.PENDING_APPROVAL.value
                or approval is None
                or approval.decision != AgentApprovalStatus.PENDING.value
            ):
                raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)

            with _open_workflow(
                workflow_factory,
                user_id=user_id,
                run_id=run.id,
                trace_sink=trace_sink,
            ) as workflow:
                inspection = workflow.inspect_durable(thread_id=thread.id)

            if inspection.status is AgentCheckpointStatus.INCONSISTENT:
                raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)
            if inspection.status is not AgentCheckpointStatus.PENDING_INTERRUPT:
                raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)
            checkpoint_approval = inspection.approval
            if (
                checkpoint_approval is None
                or checkpoint_approval.revision != approval.revision
                or checkpoint_approval.proposal_fingerprint
                != approval.proposal_fingerprint
            ):
                raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)

            preview = AgentApprovalPreview(
                run_id=run.id,
                **checkpoint_approval.preview.model_dump(
                    mode="python",
                    exclude_unset=True,
                ),
            )
        session.rollback()
        return preview
    except Exception as exc:
        session.rollback()
        if isinstance(
            exc,
            (
                AgentRunNotFoundError,
                AgentRunConflictError,
                AgentWorkflowUnavailableError,
            ),
        ):
            raise
        if isinstance(exc, AgentRecoveryLockError):
            raise AgentWorkflowUnavailableError(
                AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
            ) from None
        raise AgentWorkflowUnavailableError(
            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
        ) from None


def _stored_response(approval: AgentApproval) -> AgentApprovalResponse:
    decision = {
        AgentApprovalStatus.APPROVED.value: AgentApprovalDecision.APPROVED,
        AgentApprovalStatus.REJECTED.value: AgentApprovalDecision.REJECTED,
        AgentApprovalStatus.REQUEST_CHANGES.value: AgentApprovalDecision.REQUEST_CHANGES,
    }.get(approval.decision)
    if decision is None:
        raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)
    return AgentApprovalResponse(decision=decision, feedback=approval.feedback)


def _same_submission(
    approval: AgentApproval,
    submission: AgentApprovalSubmission,
) -> bool:
    return (
        approval.proposal_fingerprint == submission.proposal_fingerprint
        and approval.decision == submission.decision.value
        and approval.feedback == submission.feedback
    )


def _persist_progress(
    repository: AgentRunRepository,
    run: AgentRun,
    progress: AgentWorkflowProgress,
    *,
    user_id: UUID,
    id_factory: IdFactory,
    updated_at: datetime,
) -> None:
    repository.update_run(run, values=_run_values(progress), updated_at=updated_at)
    if progress.approval is None:
        return
    existing = repository.get_owned_approval(
        run_id=run.id,
        user_id=user_id,
        revision=progress.approval.revision,
    )
    if existing is None:
        repository.create_approval(
            approval_id=id_factory(),
            run_id=run.id,
            user_id=user_id,
            revision=progress.approval.revision,
            proposal_fingerprint=progress.approval.proposal_fingerprint,
        )
        return
    if (
        existing.decision != AgentApprovalStatus.PENDING.value
        or existing.proposal_fingerprint != progress.approval.proposal_fingerprint
    ):
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)


def _run_already_matches(run: AgentRun, progress: AgentWorkflowProgress) -> bool:
    values = _run_values(progress)
    return all(
        getattr(run, field_name) == value for field_name, value in values.items()
    )


def submit_agent_approval(
    run_id: UUID,
    submission: AgentApprovalSubmission,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = AgentRunRepository,
    workflow_factory: WorkflowFactory = open_runtime_workflow,
    id_factory: IdFactory = uuid4,
    clock: Clock = utc_now,
    trace_sink: TraceSink = NOOP_TRACE_SINK,
    recovery_lock_factory: RecoveryLockFactory = open_agent_recovery_lock,
    after_decision_commit: FaultHook = _no_fault,
    before_progress_commit: FaultHook = _no_fault,
) -> AgentRunSnapshot:
    """Record or recover one exact durable approval under a run-scoped lock."""

    try:
        with recovery_lock_factory(session, run_id):
            repository = repository_factory(session)
            run = repository.get_owned_run(run_id=run_id, user_id=user_id)
            if run is None:
                raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
            thread = repository.get_owned_thread(
                thread_id=run.thread_id,
                user_id=user_id,
            )
            approval = repository.get_owned_approval(
                run_id=run_id,
                user_id=user_id,
                revision=submission.revision,
            )
            if thread is None or approval is None:
                raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)

            if approval.decision == AgentApprovalStatus.PENDING.value:
                latest = repository.get_latest_owned_approval(
                    run_id=run_id,
                    user_id=user_id,
                )
                if (
                    run.status != AgentRunStatus.PENDING_APPROVAL.value
                    or latest is None
                    or latest.id != approval.id
                    or approval.proposal_fingerprint != submission.proposal_fingerprint
                ):
                    raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)
                decided_at = clock()
                repository.update_approval(
                    approval,
                    decision=submission.decision.value,
                    feedback=submission.feedback,
                    decided_at=decided_at,
                )
                repository.update_run(
                    run,
                    values={"status": AgentRunStatus.RUNNING.value},
                    updated_at=decided_at,
                )
                session.commit()
                after_decision_commit()
            elif not _same_submission(approval, submission) or run.status not in {
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.PENDING_APPROVAL.value,
                *_TERMINAL_RUN_STATUSES,
            }:
                raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)

            with _open_workflow(
                workflow_factory,
                user_id=user_id,
                run_id=run.id,
                trace_sink=trace_sink,
            ) as workflow:
                inspection = workflow.inspect_durable(thread_id=thread.id)
                if inspection.status is AgentCheckpointStatus.INCONSISTENT:
                    raise AgentWorkflowUnavailableError(
                        AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                    )
                if inspection.status is AgentCheckpointStatus.TERMINAL:
                    if inspection.output is None:
                        raise AgentWorkflowUnavailableError(
                            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                        )
                    progress = AgentWorkflowProgress(output=inspection.output)
                elif inspection.status is AgentCheckpointStatus.CONTINUABLE:
                    if run.status != AgentRunStatus.RUNNING.value:
                        raise AgentWorkflowUnavailableError(
                            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                        )
                    progress = workflow.continue_durable(thread_id=thread.id)
                else:
                    checkpoint_approval = inspection.approval
                    if checkpoint_approval is None:
                        raise AgentWorkflowUnavailableError(
                            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                        )
                    if (
                        checkpoint_approval.revision == approval.revision
                        and checkpoint_approval.proposal_fingerprint
                        == approval.proposal_fingerprint
                    ):
                        if run.status != AgentRunStatus.RUNNING.value:
                            raise AgentWorkflowUnavailableError(
                                AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                            )
                        progress = workflow.resume_durable(
                            _stored_response(approval),
                            thread_id=thread.id,
                        )
                    elif (
                        approval.decision == AgentApprovalStatus.REQUEST_CHANGES.value
                        and checkpoint_approval.revision == approval.revision + 1
                    ):
                        progress = AgentWorkflowProgress(approval=checkpoint_approval)
                    else:
                        raise AgentWorkflowUnavailableError(
                            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
                        )

            if (
                progress.output is not None
                and run.status in _TERMINAL_RUN_STATUSES
                and _run_already_matches(run, progress)
            ):
                snapshot = _snapshot(repository, run_id=run.id, user_id=user_id)
                session.rollback()
                return snapshot

            _persist_progress(
                repository,
                run,
                progress,
                user_id=user_id,
                id_factory=id_factory,
                updated_at=clock(),
            )
            before_progress_commit()
            session.commit()
            return _snapshot(repository, run_id=run.id, user_id=user_id)
    except Exception as exc:
        session.rollback()
        if isinstance(
            exc,
            (
                AgentRunNotFoundError,
                AgentRunConflictError,
                AgentWorkflowUnavailableError,
            ),
        ):
            raise
        if isinstance(exc, AgentRecoveryLockError):
            raise AgentWorkflowUnavailableError(
                AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
            ) from None
        raise AgentWorkflowUnavailableError(
            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
        ) from None
