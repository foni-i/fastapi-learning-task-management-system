"""Focused tests for durable Agent workflow orchestration."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Never, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.graph import (
    AgentCheckpointInspection,
    AgentCheckpointStatus,
    AgentWorkflow,
    AgentWorkflowProgress,
)
from app.agent.metrics import AgentRunMetrics, AgentRunOutcome
from app.agent.nodes.approval import (
    AgentApprovalInterrupt,
    AgentApprovalProposalPreview,
    CreateTaskApprovalPreview,
)
from app.agent.schemas import (
    STUDY_PLAN_PROMPT_VERSION,
    PlanningGoal,
    PlanningResult,
    PlanningStatus,
    StudyPlan,
    StudyPlanStep,
)
from app.agent.state import (
    AgentGraphOutput,
    AgentPlanProposal,
    AgentProposedAction,
    AgentTerminalStatus,
    AgentWriteToolName,
    fingerprint_plan_proposal,
)
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
from app.schemas.task import TaskCreate
from app.services.agent_workflow import (
    get_agent_approval_preview,
    get_agent_run_snapshot,
    start_agent_run,
    submit_agent_approval,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000101")


def _proposal(*, title: str = "Previewed task") -> AgentPlanProposal:
    return AgentPlanProposal(
        planning_result=PlanningResult(
            prompt_version=STUDY_PLAN_PROMPT_VERSION,
            status=PlanningStatus.COMPLETED,
            plan=StudyPlan(
                summary="Safe plan",
                steps=(
                    StudyPlanStep(
                        step_key="step_1",
                        position=1,
                        title="Study",
                        description="Complete one focused session",
                        success_criteria="Notes exist",
                    ),
                ),
            ),
        ),
        actions=(
            AgentProposedAction(
                action_key="create_1",
                tool_name=AgentWriteToolName.CREATE_TASK,
                arguments={"project_id": str(PROJECT_ID), "title": title},
            ),
        ),
    )


FINGERPRINT = fingerprint_plan_proposal(_proposal())


class RecordingSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.expirations = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def expire_all(self) -> None:
        self.expirations += 1


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
        inspection: AgentCheckpointInspection | None = None,
        continuation: AgentWorkflowProgress | None = None,
    ) -> None:
        self.start_result = start
        self.resume_result = resume
        self.inspection_result = inspection
        self.continuation_result = continuation
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

    def inspect_durable(self, *, thread_id: UUID) -> AgentCheckpointInspection:
        self.thread_ids.append(thread_id)
        if self.inspection_result is not None:
            return self.inspection_result
        return AgentCheckpointInspection(
            AgentCheckpointStatus.PENDING_INTERRUPT,
            approval=_pending().approval,
        )

    def continue_durable(self, *, thread_id: UUID) -> AgentWorkflowProgress:
        self.thread_ids.append(thread_id)
        assert self.continuation_result is not None
        return self.continuation_result


class UnavailableWorkflowContext(AbstractContextManager[AgentWorkflow]):
    def __enter__(self) -> Never:
        raise AgentWorkflowUnavailableError(AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)

    def __exit__(self, *_args: object) -> None:
        return None


def _pending(revision: int = 0, title: str = "Previewed task") -> AgentWorkflowProgress:
    proposal = _proposal(title=title)
    fingerprint = fingerprint_plan_proposal(proposal)
    return AgentWorkflowProgress(
        approval=AgentApprovalInterrupt(
            plan_summary="Safe plan",
            action_names=(AgentWriteToolName.CREATE_TASK,),
            action_count=1,
            revision=revision,
            proposal_fingerprint=fingerprint,
            preview=AgentApprovalProposalPreview(
                revision=revision,
                proposal_fingerprint=fingerprint,
                planning_result=proposal.planning_result,
                actions=(
                    CreateTaskApprovalPreview(
                        action_key="create_1",
                        tool_name=AgentWriteToolName.CREATE_TASK,
                        task=TaskCreate(
                            project_id=PROJECT_ID,
                            title=title,
                        ),
                    ),
                ),
            ),
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


@contextmanager
def _recovery_lock(_session: Session, _run_id: UUID) -> Iterator[None]:
    yield


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


def test_owned_pending_preview_is_exact_and_read_only() -> None:
    user_id, thread_id, run_id, repository = _started()
    session = RecordingSession()
    workflow = ScriptedWorkflow()

    preview = get_agent_approval_preview(
        run_id,
        user_id,
        cast(Session, session),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(workflow),
        recovery_lock_factory=_recovery_lock,
    )

    assert preview.run_id == run_id
    assert preview.revision == 0
    assert preview.proposal_fingerprint == FINGERPRINT
    assert preview.to_plan_proposal() == _proposal()
    assert workflow.thread_ids == [thread_id]
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.expirations == 1
    assert "user_id" not in preview.model_dump_json()


def test_foreign_preview_is_404_before_lock_or_checkpoint_open() -> None:
    _user_id, _thread_id, run_id, repository = _started()
    lock_calls: list[UUID] = []

    @contextmanager
    def forbidden_lock(_session: Session, _run_id: UUID) -> Iterator[None]:
        lock_calls.append(_run_id)
        yield

    with pytest.raises(AgentRunNotFoundError):
        get_agent_approval_preview(
            run_id,
            uuid4(),
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=cast(
                Callable[[UUID], AbstractContextManager[AgentWorkflow]],
                lambda _user_id: pytest.fail("must not inspect checkpoint"),
            ),
            recovery_lock_factory=forbidden_lock,
        )
    assert lock_calls == []


@pytest.mark.parametrize(
    "inspection",
    [
        AgentCheckpointInspection(AgentCheckpointStatus.TERMINAL),
        AgentCheckpointInspection(AgentCheckpointStatus.CONTINUABLE),
    ],
)
def test_non_pending_preview_checkpoint_is_conflict(
    inspection: AgentCheckpointInspection,
) -> None:
    user_id, _thread_id, run_id, repository = _started()
    with pytest.raises(AgentRunConflictError):
        get_agent_approval_preview(
            run_id,
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=_factory(ScriptedWorkflow(inspection=inspection)),
            recovery_lock_factory=_recovery_lock,
        )


def test_inconsistent_preview_checkpoint_is_safe_unavailable() -> None:
    user_id, _thread_id, run_id, repository = _started()
    with pytest.raises(
        AgentWorkflowUnavailableError,
        match=AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    ):
        get_agent_approval_preview(
            run_id,
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=_factory(
                ScriptedWorkflow(
                    inspection=AgentCheckpointInspection(
                        AgentCheckpointStatus.INCONSISTENT
                    )
                )
            ),
            recovery_lock_factory=_recovery_lock,
        )


def test_preview_product_or_checkpoint_mismatch_is_conflict() -> None:
    user_id, _thread_id, run_id, repository = _started()
    assert repository.run is not None
    repository.run.status = "SUCCEEDED"
    with pytest.raises(AgentRunConflictError):
        get_agent_approval_preview(
            run_id,
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=cast(
                Callable[[UUID], AbstractContextManager[AgentWorkflow]],
                lambda _user_id: pytest.fail("must not inspect decided run"),
            ),
            recovery_lock_factory=_recovery_lock,
        )

    repository.run.status = "PENDING_APPROVAL"
    mismatched = ScriptedWorkflow(
        inspection=AgentCheckpointInspection(
            AgentCheckpointStatus.PENDING_INTERRUPT,
            approval=_pending(title="Different proposal").approval,
        )
    )
    with pytest.raises(AgentRunConflictError):
        get_agent_approval_preview(
            run_id,
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=_factory(mismatched),
            recovery_lock_factory=_recovery_lock,
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
        recovery_lock_factory=_recovery_lock,
    )
    assert workflow.thread_ids == [thread_id, thread_id]
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
        workflow_factory=_factory(
            ScriptedWorkflow(resume=_pending(1, "Revised previewed task"))
        ),
        id_factory=_ids(uuid4()),
        clock=lambda: NOW,
        recovery_lock_factory=_recovery_lock,
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
        recovery_lock_factory=_recovery_lock,
    )
    assert result.run.metrics.input_tokens == 0
    assert result.run.metrics.output_tokens == 0
    assert result.run.metrics.total_tokens == 0


def test_resume_infrastructure_failure_keeps_decision_recoverable() -> None:
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
            recovery_lock_factory=_recovery_lock,
        )

    assert repository.run is not None
    assert repository.run.status == "RUNNING"
    assert repository.run.error_code is None
    assert repository.approvals[0].decision == "APPROVED"
    assert session.commits == 1
    assert session.rollbacks == 1


@pytest.mark.parametrize("mutation", ["status", "fingerprint"])
def test_pending_status_and_stale_fingerprint_are_safe_conflicts(mutation: str) -> None:
    user_id, _thread_id, run_id, repository = _started()
    assert repository.run is not None
    if mutation == "status":
        repository.run.status = "SUCCEEDED"
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
            recovery_lock_factory=_recovery_lock,
        )
    assert session.commits == 0
    assert session.rollbacks == 1


def test_same_terminal_submission_returns_snapshot_without_resume() -> None:
    user_id, _thread_id, run_id, repository = _started()
    assert repository.run is not None
    approval = repository.approvals[0]
    approval.decision = "APPROVED"
    approval.decided_at = NOW
    repository.run.status = "SUCCEEDED"
    workflow = ScriptedWorkflow(
        inspection=AgentCheckpointInspection(
            AgentCheckpointStatus.TERMINAL,
            output=_terminal(AgentTerminalStatus.SUCCEEDED).output,
        )
    )

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
        workflow_factory=_factory(workflow),
        recovery_lock_factory=_recovery_lock,
        clock=lambda: NOW,
    )

    assert result.run.status.value == "SUCCEEDED"
    assert workflow.responses == []


def test_different_decided_submission_is_conflict_without_workflow() -> None:
    user_id, _thread_id, run_id, repository = _started()
    approval = repository.approvals[0]
    approval.decision = "REJECTED"
    approval.decided_at = NOW
    assert repository.run is not None
    repository.run.status = "RUNNING"

    with pytest.raises(AgentRunConflictError):
        submit_agent_approval(
            run_id,
            AgentApprovalSubmission(
                revision=0,
                proposal_fingerprint=FINGERPRINT,
                decision=AgentApprovalSubmissionDecision.APPROVED,
            ),
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=cast(
                Callable[[UUID], AbstractContextManager[AgentWorkflow]],
                lambda _user_id: pytest.fail("must not inspect or resume"),
            ),
            recovery_lock_factory=_recovery_lock,
        )


class SimulatedProcessTermination(BaseException):
    """Escape ordinary Exception handlers at the exact post-commit seam."""


def test_post_decision_commit_termination_recovers_with_same_submission() -> None:
    user_id, thread_id, run_id, repository = _started()
    submission = AgentApprovalSubmission(
        revision=0,
        proposal_fingerprint=FINGERPRINT,
        decision=AgentApprovalSubmissionDecision.APPROVED,
    )
    first_session = RecordingSession()

    with pytest.raises(SimulatedProcessTermination):
        submit_agent_approval(
            run_id,
            submission,
            user_id,
            cast(Session, first_session),
            repository_factory=_repo_factory(repository),
            workflow_factory=cast(
                Callable[[UUID], AbstractContextManager[AgentWorkflow]],
                lambda _user_id: pytest.fail("fault occurs before workflow open"),
            ),
            recovery_lock_factory=_recovery_lock,
            after_decision_commit=lambda: (_ for _ in ()).throw(
                SimulatedProcessTermination()
            ),
            clock=lambda: NOW,
        )

    assert repository.run is not None
    assert repository.run.status == "RUNNING"
    assert repository.approvals[0].decision == "APPROVED"
    assert first_session.commits == 1
    assert first_session.rollbacks == 0

    rebuilt = ScriptedWorkflow(resume=_terminal(AgentTerminalStatus.SUCCEEDED))
    recovered = submit_agent_approval(
        run_id,
        submission,
        user_id,
        cast(Session, RecordingSession()),
        repository_factory=_repo_factory(repository),
        workflow_factory=_factory(rebuilt),
        recovery_lock_factory=_recovery_lock,
        clock=lambda: NOW,
    )

    assert recovered.run.status.value == "SUCCEEDED"
    assert rebuilt.thread_ids == [thread_id, thread_id]
    assert len(rebuilt.responses) == 1


def test_continuable_checkpoint_uses_public_continuation_without_resume() -> None:
    user_id, thread_id, run_id, repository = _started()
    approval = repository.approvals[0]
    approval.decision = "APPROVED"
    approval.decided_at = NOW
    assert repository.run is not None
    repository.run.status = "RUNNING"
    workflow = ScriptedWorkflow(
        inspection=AgentCheckpointInspection(AgentCheckpointStatus.CONTINUABLE),
        continuation=_terminal(AgentTerminalStatus.SUCCEEDED),
    )

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
        workflow_factory=_factory(workflow),
        recovery_lock_factory=_recovery_lock,
        clock=lambda: NOW,
    )

    assert result.run.status.value == "SUCCEEDED"
    assert workflow.thread_ids == [thread_id, thread_id]
    assert workflow.responses == []


def test_inconsistent_checkpoint_fails_closed_without_raw_state() -> None:
    user_id, _thread_id, run_id, repository = _started()
    submission = AgentApprovalSubmission(
        revision=0,
        proposal_fingerprint=FINGERPRINT,
        decision=AgentApprovalSubmissionDecision.APPROVED,
    )
    workflow = ScriptedWorkflow(
        inspection=AgentCheckpointInspection(AgentCheckpointStatus.INCONSISTENT)
    )

    with pytest.raises(AgentWorkflowUnavailableError) as error:
        submit_agent_approval(
            run_id,
            submission,
            user_id,
            cast(Session, RecordingSession()),
            repository_factory=_repo_factory(repository),
            workflow_factory=_factory(workflow),
            recovery_lock_factory=_recovery_lock,
            clock=lambda: NOW,
        )

    assert str(error.value) == AGENT_WORKFLOW_UNAVAILABLE_MESSAGE
    assert "checkpoint" not in str(error.value).casefold()
    assert repository.run is not None
    assert repository.run.status == "RUNNING"
