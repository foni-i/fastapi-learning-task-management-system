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
from app.agent.graph import AgentWorkflow, AgentWorkflowProgress, build_agent_graph
from app.agent.nodes.approval import (
    AgentApprovalRequest,
    AgentApprovalResponse,
    ApprovalDecider,
)
from app.agent.providers import OpenAIProvider
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION
from app.agent.state import AgentApprovalDecision, AgentGraphInput, AgentTerminalStatus
from app.core.config import get_settings
from app.core.exceptions import (
    AGENT_RUN_CONFLICT_MESSAGE,
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentWorkflowUnavailableError,
)
from app.models.agent_run import AgentApprovalStatus, AgentRunStatus
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentApprovalSubmission,
    AgentApprovalSubmissionDecision,
    AgentRunSnapshot,
    AgentRunStartRequest,
    PublicAgentApproval,
    PublicAgentRun,
    PublicAgentThread,
)
from app.services.agent_domain import AgentDomainGateway

RepositoryFactory = Callable[[Session], AgentRunRepository]
IdFactory = Callable[[], UUID]
Clock = Callable[[], datetime]
WorkflowFactory = Callable[[UUID], AbstractContextManager[AgentWorkflow]]


class _DurableApprovalOnly(ApprovalDecider):
    def decide(self, request: AgentApprovalRequest) -> Never:
        raise RuntimeError("Durable workflow must use LangGraph interrupt")


def utc_now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def open_runtime_workflow(user_id: UUID) -> Iterator[AgentWorkflow]:
    """Build one production workflow without exposing credentials or connections."""

    settings = get_settings()
    if (
        settings.model_provider != "openai"
        or settings.model_name is None
        or settings.model_api_key is None
    ):
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)
    try:
        with open_postgres_checkpointer() as checkpointer:
            yield build_agent_graph(
                model=settings.model_name,
                provider=OpenAIProvider(api_key=settings.model_api_key),
                gateway=AgentDomainGateway(),
                runtime_context=AgentRuntimeContext(
                    user_id=user_id,
                    write_tools_enabled=True,
                ),
                approval_decider=_DurableApprovalOnly(),
                clock=monotonic,
                sleeper=sleep,
                checkpointer=checkpointer,
                durable_approval=True,
            )
    except AgentWorkflowUnavailableError:
        raise
    except Exception:
        raise AgentWorkflowUnavailableError(
            AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
        ) from None


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
        metrics = progress.output.metrics.model_dump()
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
        with workflow_factory(user_id) as workflow:
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


def _internal_response(submission: AgentApprovalSubmission) -> AgentApprovalResponse:
    decision = {
        AgentApprovalSubmissionDecision.APPROVED: AgentApprovalDecision.APPROVED,
        AgentApprovalSubmissionDecision.REJECTED: AgentApprovalDecision.REJECTED,
        AgentApprovalSubmissionDecision.REQUEST_CHANGES: (
            AgentApprovalDecision.REQUEST_CHANGES
        ),
    }[submission.decision]
    return AgentApprovalResponse(decision=decision, feedback=submission.feedback)


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
) -> AgentRunSnapshot:
    """Decide one exact pending approval, then resume its durable graph once."""

    repository = repository_factory(session)
    run = repository.get_owned_run(run_id=run_id, user_id=user_id)
    if run is None:
        raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    thread = repository.get_owned_thread(thread_id=run.thread_id, user_id=user_id)
    approval = repository.get_owned_approval(
        run_id=run_id,
        user_id=user_id,
        revision=submission.revision,
    )
    if (
        thread is None
        or run.status != AgentRunStatus.PENDING_APPROVAL.value
        or approval is None
        or approval.decision != AgentApprovalStatus.PENDING.value
        or approval.proposal_fingerprint != submission.proposal_fingerprint
    ):
        raise AgentRunConflictError(AGENT_RUN_CONFLICT_MESSAGE)

    decided_at = clock()
    try:
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
    except Exception:
        session.rollback()
        raise

    try:
        with workflow_factory(user_id) as workflow:
            progress = workflow.resume_durable(
                _internal_response(submission),
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
        try:
            repository.update_run(
                run,
                values={
                    "status": AgentRunStatus.FAILED.value,
                    "current_node": "request_approval",
                    "error_code": "AGENT_RESUME_FAILED",
                },
                updated_at=clock(),
            )
            session.commit()
        except Exception:
            session.rollback()
        raise
