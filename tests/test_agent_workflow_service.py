"""Focused tests for durable Agent workflow orchestration."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Never, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.graph import AgentWorkflow, AgentWorkflowProgress
from app.agent.metrics import AgentRunMetrics, AgentRunOutcome
from app.agent.nodes.approval import AgentApprovalInterrupt
from app.agent.schemas import PlanningGoal
from app.agent.state import AgentGraphOutput, AgentTerminalStatus, AgentWriteToolName
from app.core.exceptions import (
    AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentWorkflowUnavailableError,
)
from app.models.agent_run import AgentApproval, AgentRun, AgentThread
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentApprovalSubmission,
    AgentApprovalSubmissionDecision,
    AgentRunStartRequest,
)
from app.services.agent_workflow import (
    get_agent_run_snapshot,
    start_agent_run,
    submit_agent_approval,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)
FINGERPRINT = "a" * 64


class RecordingSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class MemoryRepository:
    def __init__(self) -> None:
        self.thread: AgentThread | None = None
        self.run: AgentRun | None = None
        self.approvals: list[AgentApproval] = []
        self.calls: list[str] = []

    def create_thread(
        self, *, thread_id: UUID, user_id: UUID, goal_summary: str
    ) -> AgentThread:
        self.calls.append("create_thread")
        self.thread = AgentThread(
            id=thread_id,
            user_id=user_id,
            goal_summary=goal_summary,
            status="ACTIVE",
            created_at=NOW,
            updated_at=NOW,
        )
        return self.thread

    def create_run(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        user_id: UUID,
        prompt_version: str,
    ) -> AgentRun:
        self.calls.append("create_run")
        self.run = AgentRun(
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
        return self.run

    def create_approval(
        self,
        *,
        approval_id: UUID,
        run_id: UUID,
        user_id: UUID,
        revision: int,
        proposal_fingerprint: str,
    ) -> AgentApproval:
        self.calls.append("create_approval")
        approval = AgentApproval(
            id=approval_id,
            run_id=run_id,
            user_id=user_id,
            revision=revision,
            proposal_fingerprint=proposal_fingerprint,
            decision="PENDING",
            feedback=None,
            decided_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.approvals.append(approval)
        return approval

    def get_owned_thread(self, *, thread_id: UUID, user_id: UUID) -> AgentThread | None:
        self.calls.append("get_thread")
        if self.thread is None:
            return None
        return (
            self.thread
            if (self.thread.id, self.thread.user_id) == (thread_id, user_id)
            else None
        )

    def get_owned_run(self, *, run_id: UUID, user_id: UUID) -> AgentRun | None:
        self.calls.append("get_run")
        if self.run is None:
            return None
        return (
            self.run if (self.run.id, self.run.user_id) == (run_id, user_id) else None
        )

    def get_owned_approval(
        self, *, run_id: UUID, user_id: UUID, revision: int
    ) -> AgentApproval | None:
        return next(
            (
                item
                for item in self.approvals
                if (item.run_id, item.user_id, item.revision)
                == (run_id, user_id, revision)
            ),
            None,
        )

    def get_latest_owned_approval(
        self, *, run_id: UUID, user_id: UUID
    ) -> AgentApproval | None:
        matches = [
            item
            for item in self.approvals
            if (item.run_id, item.user_id) == (run_id, user_id)
        ]
        return max(matches, key=lambda item: item.revision, default=None)

    def update_run(
        self, run: AgentRun, *, values: dict[str, object], updated_at: datetime
    ) -> AgentRun:
        self.calls.append("update_run")
        for name, value in values.items():
            setattr(run, name, value)
        run.updated_at = updated_at
        return run

    def update_approval(
        self,
        approval: AgentApproval,
        *,
        decision: str,
        feedback: str | None,
        decided_at: datetime,
    ) -> AgentApproval:
        self.calls.append("update_approval")
        approval.decision = decision
        approval.feedback = feedback
        approval.decided_at = decided_at
        approval.updated_at = decided_at
        return approval


class ScriptedWorkflow:
    def __init__(
        self,
        *,
        start: AgentWorkflowProgress | None = None,
        resume: AgentWorkflowProgress | None = None,
    ) -> None:
        self.start_result = start
        self.resume_result = resume
        self.thread_ids: list[UUID] = []
        self.responses: list[object] = []

    def start_durable(
        self, graph_input: object, *, thread_id: UUID
    ) -> AgentWorkflowProgress:
        self.thread_ids.append(thread_id)
        assert self.start_result is not None
        return self.start_result

    def resume_durable(
        self, response: object, *, thread_id: UUID
    ) -> AgentWorkflowProgress:
        self.thread_ids.append(thread_id)
        self.responses.append(response)
        assert self.resume_result is not None
        return self.resume_result


class UnavailableWorkflowContext(AbstractContextManager[AgentWorkflow]):
    def __enter__(self) -> Never:
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)

    def __exit__(self, *_args: object) -> None:
        return None


def _pending(
    revision: int = 0, fingerprint: str = FINGERPRINT
) -> AgentWorkflowProgress:
    return AgentWorkflowProgress(
        approval=AgentApprovalInterrupt(
            plan_summary="Safe plan",
            action_names=(AgentWriteToolName.CREATE_TASK,),
            action_count=1,
            revision=revision,
            proposal_fingerprint=fingerprint,
        )
    )


def _terminal(
    status: AgentTerminalStatus,
    *,
    metrics: AgentRunMetrics | None = None,
) -> AgentWorkflowProgress:
    return AgentWorkflowProgress(
        output=AgentGraphOutput(status=status, summary="Safe result", metrics=metrics)
    )


def _factory(
    workflow: ScriptedWorkflow,
) -> Callable[[UUID], AbstractContextManager[AgentWorkflow]]:
    @contextmanager
    def open_workflow(user_id: UUID) -> Iterator[AgentWorkflow]:
        assert isinstance(user_id, UUID)
        yield cast(AgentWorkflow, workflow)

    return open_workflow


def _repo_factory(
    repository: MemoryRepository,
) -> Callable[[Session], AgentRunRepository]:
    return cast(Callable[[Session], AgentRunRepository], lambda _session: repository)


def _ids(*values: UUID) -> Callable[[], UUID]:
    iterator = iter(values)
    return lambda: next(iterator)


def _started() -> tuple[UUID, UUID, UUID, MemoryRepository]:
    user_id, thread_id, run_id = uuid4(), uuid4(), uuid4()
    repository = MemoryRepository()
    start_agent_run(
        AgentRunStartRequest(goal=PlanningGoal(objective="Learn durable graphs")),
        user_id,
        cast(Session, RecordingSession()),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(ScriptedWorkflow(start=_pending())),
        id_factory=_ids(thread_id, run_id, uuid4()),
        clock=lambda: NOW,
    )
    return user_id, thread_id, run_id, repository


def test_start_creates_owned_records_interrupt_and_one_product_transaction() -> None:
    user_id, thread_id, run_id = uuid4(), uuid4(), uuid4()
    repository = MemoryRepository()
    session = RecordingSession()
    workflow = ScriptedWorkflow(start=_pending())

    snapshot = start_agent_run(
        AgentRunStartRequest(goal=PlanningGoal(objective="Learn durable graphs")),
        user_id,
        cast(Session, session),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(workflow),
        id_factory=_ids(thread_id, run_id, uuid4()),
        clock=lambda: NOW,
    )

    assert workflow.thread_ids == [thread_id]
    assert repository.calls[:4] == [
        "create_thread",
        "create_run",
        "update_run",
        "create_approval",
    ]
    assert snapshot.run.id == run_id
    assert snapshot.run.status.value == "PENDING_APPROVAL"
    assert snapshot.approval is not None
    assert snapshot.approval.decision.value == "PENDING"
    assert session.commits == 1
    assert session.rollbacks == 0


def test_start_failure_rolls_back_without_commit() -> None:
    session = RecordingSession()

    with pytest.raises(AssertionError):
        start_agent_run(
            AgentRunStartRequest(goal=PlanningGoal(objective="Learn")),
            uuid4(),
            cast(Session, session),
            repository_factory=_repo_factory(MemoryRepository()),
            workflow_factory=_factory(ScriptedWorkflow()),
        )
    assert session.commits == 0
    assert session.rollbacks == 1


def test_owned_snapshot_is_read_only_and_foreign_is_safe_not_found() -> None:
    user_id, _thread_id, run_id, repository = _started()
    session = RecordingSession()
    snapshot = get_agent_run_snapshot(
        run_id,
        user_id,
        cast(Session, session),
        repository_factory=_repo_factory(repository),
    )
    assert snapshot.run.id == run_id
    assert session.commits == session.rollbacks == 0
    with pytest.raises(AgentRunNotFoundError):
        get_agent_run_snapshot(
            run_id,
            uuid4(),
            cast(Session, session),
            repository_factory=_repo_factory(repository),
        )


@pytest.mark.parametrize(
    ("decision", "terminal"),
    [
        (AgentApprovalSubmissionDecision.APPROVED, AgentTerminalStatus.SUCCEEDED),
        (AgentApprovalSubmissionDecision.REJECTED, AgentTerminalStatus.REJECTED),
    ],
)
def test_approve_and_reject_decide_once_then_resume(
    decision: AgentApprovalSubmissionDecision,
    terminal: AgentTerminalStatus,
) -> None:
    user_id, thread_id, run_id, repository = _started()
    session = RecordingSession()
    workflow = ScriptedWorkflow(resume=_terminal(terminal))
    result = submit_agent_approval(
        run_id,
        AgentApprovalSubmission(
            revision=0,
            proposal_fingerprint=FINGERPRINT,
            decision=decision,
        ),
        user_id,
        cast(Session, session),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(workflow),
        clock=lambda: NOW,
    )
    assert workflow.thread_ids == [thread_id]
    assert result.run.status.value == terminal.value.upper()
    assert result.approval is not None
    assert result.approval.decision.value == decision.value
    assert session.commits == 2
    assert session.rollbacks == 0


def test_request_changes_creates_next_pending_revision() -> None:
    user_id, _thread_id, run_id, repository = _started()
    session = RecordingSession()
    result = submit_agent_approval(
        run_id,
        AgentApprovalSubmission(
            revision=0,
            proposal_fingerprint=FINGERPRINT,
            decision=AgentApprovalSubmissionDecision.REQUEST_CHANGES,
            feedback="  Make it shorter  ",
        ),
        user_id,
        cast(Session, session),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(ScriptedWorkflow(resume=_pending(1, "b" * 64))),
        id_factory=_ids(uuid4()),
        clock=lambda: NOW,
    )
    assert result.run.status.value == "PENDING_APPROVAL"
    assert result.approval is not None
    assert result.approval.revision == 1
    assert result.approval.decision.value == "PENDING"
    assert repository.approvals[0].feedback == "Make it shorter"


def test_missing_provider_token_counts_persist_as_zero() -> None:
    user_id, _thread_id, run_id, repository = _started()
    result = submit_agent_approval(
        run_id,
        AgentApprovalSubmission(
            revision=0,
            proposal_fingerprint=FINGERPRINT,
            decision=AgentApprovalSubmissionDecision.APPROVED,
        ),
        user_id,
        cast(Session, RecordingSession()),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(
            ScriptedWorkflow(
                resume=_terminal(
                    AgentTerminalStatus.SUCCEEDED,
                    metrics=AgentRunMetrics(
                        prompt_version="study-plan.v1",
                        outcome=AgentRunOutcome.SUCCEEDED,
                        model_round_count=1,
                        provider_attempt_count=1,
                        tool_call_count=0,
                        latency_ms=1,
                    ),
                )
            )
        ),
        clock=lambda: NOW,
    )
    assert result.run.metrics.input_tokens == 0
    assert result.run.metrics.output_tokens == 0
    assert result.run.metrics.total_tokens == 0


def test_resume_infrastructure_failure_records_safe_terminal_run() -> None:
    user_id, _thread_id, run_id, repository = _started()
    session = RecordingSession()

    def unavailable(_user_id: UUID) -> AbstractContextManager[AgentWorkflow]:
        return UnavailableWorkflowContext()

    with pytest.raises(AgentWorkflowUnavailableError):
        submit_agent_approval(
            run_id,
            AgentApprovalSubmission(
                revision=0,
                proposal_fingerprint=FINGERPRINT,
                decision=AgentApprovalSubmissionDecision.APPROVED,
            ),
            user_id,
            cast(Session, session),
            repository_factory=_repo_factory(repository),
            workflow_factory=unavailable,
            clock=lambda: NOW,
        )

    assert repository.run is not None
    assert repository.run.status == "FAILED"
    assert repository.run.error_code == "AGENT_RESUME_FAILED"
    assert repository.approvals[0].decision == "APPROVED"
    assert session.commits == 2
    assert session.rollbacks == 1


@pytest.mark.parametrize("mutation", ["status", "decision", "fingerprint"])
def test_duplicate_terminal_and_stale_approval_are_safe_conflicts(
    mutation: str,
) -> None:
    user_id, _thread_id, run_id, repository = _started()
    assert repository.run is not None
    if mutation == "status":
        repository.run.status = "SUCCEEDED"
    elif mutation == "decision":
        repository.approvals[0].decision = "APPROVED"
    payload_fingerprint = "b" * 64 if mutation == "fingerprint" else FINGERPRINT
    session = RecordingSession()
    with pytest.raises(AgentRunConflictError):
        submit_agent_approval(
            run_id,
            AgentApprovalSubmission(
                revision=0,
                proposal_fingerprint=payload_fingerprint,
                decision=AgentApprovalSubmissionDecision.APPROVED,
            ),
            user_id,
            cast(Session, session),
            repository_factory=_repo_factory(repository),
            workflow_factory=cast(
                Callable[[UUID], AbstractContextManager[AgentWorkflow]],
                lambda _user_id: pytest.fail("must not resume"),
            ),
        )
    assert session.commits == session.rollbacks == 0
