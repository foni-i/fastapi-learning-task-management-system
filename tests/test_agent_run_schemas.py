"""Strict public contracts for Agent business records."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue, ValidationError

from app.agent.nodes.approval import (
    AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE,
    MAX_APPROVAL_PREVIEW_BYTES,
    BatchCreateTasksApprovalPreview,
    CreateTaskApprovalPreview,
    DeleteTaskApprovalPreview,
    UpdateTaskApprovalPreview,
)
from app.agent.schemas import (
    STUDY_PLAN_PROMPT_VERSION,
    PlanningResult,
    PlanningStatus,
    StudyPlan,
    StudyPlanStep,
)
from app.agent.state import (
    AgentPlanProposal,
    AgentProposedAction,
    AgentWriteToolName,
    fingerprint_plan_proposal,
)
from app.models.agent_run import AgentApprovalStatus, AgentRunStatus, AgentThreadStatus
from app.schemas.agent_run import (
    AgentApprovalPreview,
    AgentApprovalSubmission,
    AgentApprovalSubmissionDecision,
    AgentRunMetricsSnapshot,
    AgentRunNode,
    AgentRunSnapshot,
    AgentRunStartRequest,
    AgentThreadCreate,
    AgentThreadRunResult,
    PublicAgentApproval,
    PublicAgentRun,
    PublicAgentThread,
)
from app.schemas.task import TaskCreate, TaskUpdate

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _planning_result() -> PlanningResult:
    return PlanningResult(
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
    )


def test_thread_create_is_strict_bounded_and_trimmed() -> None:
    assert AgentThreadCreate(goal_summary="  Build a plan  ").goal_summary == (
        "Build a plan"
    )
    for payload in (
        {"goal_summary": " "},
        {"goal_summary": "x" * 2001},
        {"goal_summary": "Plan", "user_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            AgentThreadCreate.model_validate(payload)


def test_run_start_accepts_only_a_strict_goal_without_identity() -> None:
    request = AgentRunStartRequest.model_validate(
        {"goal": {"objective": "  Learn recovery  "}}
    )
    assert request.goal.objective == "Learn recovery"
    for forbidden in ("user_id", "thread_id", "run_id", "checkpoint_id"):
        with pytest.raises(ValidationError):
            AgentRunStartRequest.model_validate(
                {"goal": {"objective": "Learn"}, forbidden: str(uuid4())}
            )


def test_approval_submission_is_strict_and_normalizes_change_feedback() -> None:
    changed = AgentApprovalSubmission(
        revision=1,
        proposal_fingerprint="a" * 64,
        decision=AgentApprovalSubmissionDecision.REQUEST_CHANGES,
        feedback="  Make it smaller  ",
    )
    assert changed.feedback == "Make it smaller"
    for decision in (
        AgentApprovalSubmissionDecision.APPROVED,
        AgentApprovalSubmissionDecision.REJECTED,
    ):
        accepted = AgentApprovalSubmission(
            revision=0,
            proposal_fingerprint="b" * 64,
            decision=decision,
        )
        assert accepted.feedback is None


@pytest.mark.parametrize(
    "payload",
    [
        {"revision": 0, "proposal_fingerprint": "a" * 64, "decision": "PENDING"},
        {"revision": 3, "proposal_fingerprint": "a" * 64, "decision": "APPROVED"},
        {"revision": 0, "proposal_fingerprint": "bad", "decision": "APPROVED"},
        {
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "APPROVED",
            "feedback": "forbidden",
        },
        {
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "REQUEST_CHANGES",
        },
        {
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "REJECTED",
            "user_id": str(uuid4()),
        },
    ],
)
def test_approval_submission_rejects_stale_or_internal_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AgentApprovalSubmission.model_validate(payload)


def test_metrics_are_explicit_nonnegative_and_consistent() -> None:
    metrics = AgentRunMetricsSnapshot(input_tokens=2, output_tokens=3, total_tokens=5)
    assert set(metrics.model_dump()) == {
        "model_round_count",
        "provider_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }
    for payload in (
        {"tool_call_count": -1},
        {"input_tokens": 2, "output_tokens": 3, "total_tokens": 4},
        {"total_tokens": 0, "raw_response": "forbidden"},
    ):
        with pytest.raises(ValidationError):
            AgentRunMetricsSnapshot.model_validate(payload)


def test_public_thread_reads_attributes_and_hides_owner() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        goal_summary="Safe goal",
        status=AgentThreadStatus.ACTIVE.value,
        created_at=NOW,
        updated_at=NOW,
        raw_prompt="forbidden",
    )
    public = PublicAgentThread.model_validate(source)
    assert set(public.model_dump()) == {
        "id",
        "goal_summary",
        "status",
        "created_at",
        "updated_at",
    }
    assert "user_id" not in public.model_dump_json()


def test_public_run_collects_flat_orm_metrics_and_has_exact_whitelist() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        thread_id=uuid4(),
        user_id=uuid4(),
        status=AgentRunStatus.RUNNING.value,
        current_node="generate_plan",
        summary=None,
        error_code=None,
        prompt_version="study-plan-v1",
        model_round_count=1,
        provider_attempt_count=1,
        tool_call_count=0,
        input_tokens=7,
        output_tokens=5,
        total_tokens=12,
        latency_ms=2.5,
        created_at=NOW,
        updated_at=NOW,
        checkpoint={"forbidden": True},
    )
    public = PublicAgentRun.model_validate(source)
    assert set(public.model_dump()) == {
        "id",
        "thread_id",
        "status",
        "current_node",
        "summary",
        "error_code",
        "prompt_version",
        "metrics",
        "created_at",
        "updated_at",
    }
    serialized = public.model_dump_json()
    for forbidden in (
        "user_id",
        "checkpoint",
        "raw_prompt",
        "raw_response",
        "access_token",
    ):
        assert forbidden not in serialized.lower()


def test_thread_run_result_is_strict_and_contains_only_public_records() -> None:
    thread_id = uuid4()
    thread = PublicAgentThread(
        id=thread_id,
        goal_summary="Goal",
        status=AgentThreadStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    run = PublicAgentRun.model_validate(
        SimpleNamespace(
            id=uuid4(),
            thread_id=thread_id,
            user_id=uuid4(),
            status="PENDING",
            current_node=None,
            summary=None,
            error_code=None,
            prompt_version="study-plan.v1",
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
    )

    result = AgentThreadRunResult(thread=thread, run=run)

    assert set(result.model_dump()) == {"thread", "run"}
    serialized = result.model_dump_json().lower()
    for forbidden in (
        "user_id",
        "checkpoint",
        "raw_prompt",
        "model_response",
        "access_token",
        "authorization",
        "session",
    ):
        assert forbidden not in serialized
    with pytest.raises(ValidationError):
        AgentThreadRunResult.model_validate(
            {"thread": thread, "run": run, "user_id": str(uuid4())}
        )


def test_run_snapshot_contains_only_public_product_records() -> None:
    thread_id = uuid4()
    thread = PublicAgentThread(
        id=thread_id,
        goal_summary="Goal",
        status=AgentThreadStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    run = PublicAgentRun(
        id=uuid4(),
        thread_id=thread_id,
        status=AgentRunStatus.PENDING_APPROVAL,
        current_node=AgentRunNode.REQUEST_APPROVAL,
        summary=None,
        error_code=None,
        prompt_version="study-plan.v1",
        metrics=AgentRunMetricsSnapshot(),
        created_at=NOW,
        updated_at=NOW,
    )
    snapshot = AgentRunSnapshot(thread=thread, run=run)
    assert set(snapshot.model_dump()) == {"thread", "run", "approval"}
    serialized = snapshot.model_dump_json().lower()
    for forbidden in ("user_id", "checkpoint", "arguments", "raw_prompt", "reasoning"):
        assert forbidden not in serialized


def test_approval_preview_is_typed_exact_and_preserves_update_null() -> None:
    project_id, task_id = uuid4(), uuid4()
    batch_tasks: list[dict[str, object]] = [
        {"project_id": str(project_id), "title": "Batch one"},
        {"project_id": str(project_id), "title": "Batch two"},
    ]
    action_payloads: tuple[dict[str, object], ...] = (
        {
            "action_key": "create_1",
            "tool_name": AgentWriteToolName.CREATE_TASK,
            "arguments": {"project_id": str(project_id), "title": "Create"},
        },
        {
            "action_key": "update_1",
            "tool_name": AgentWriteToolName.UPDATE_TASK,
            "arguments": {"task_id": str(task_id), "description": None},
        },
        {
            "action_key": "batch_1",
            "tool_name": AgentWriteToolName.BATCH_CREATE_TASKS,
            "arguments": {"tasks": batch_tasks},
        },
    )
    proposal = AgentPlanProposal(
        planning_result=_planning_result(),
        actions=tuple(
            AgentProposedAction.model_validate(item) for item in action_payloads
        ),
    )
    preview = AgentApprovalPreview(
        run_id=uuid4(),
        revision=1,
        proposal_fingerprint=fingerprint_plan_proposal(proposal),
        planning_result=proposal.planning_result,
        actions=(
            CreateTaskApprovalPreview(
                action_key="create_1",
                tool_name=AgentWriteToolName.CREATE_TASK,
                task=TaskCreate.model_validate(action_payloads[0]["arguments"]),
            ),
            UpdateTaskApprovalPreview(
                action_key="update_1",
                tool_name=AgentWriteToolName.UPDATE_TASK,
                task_id=task_id,
                changes=TaskUpdate(description=None),
            ),
            BatchCreateTasksApprovalPreview(
                action_key="batch_1",
                tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
                tasks=tuple(TaskCreate.model_validate(item) for item in batch_tasks),
            ),
        ),
    )

    assert preview.to_plan_proposal() == proposal
    serialized = preview.model_dump(mode="json", exclude_unset=True)
    changes = serialized["actions"][1]["changes"]
    assert changes == {"description": None}
    serialized_json = preview.model_dump_json().lower()
    for forbidden in ('"user_id"', '"arguments"', '"checkpoint"', '"session"'):
        assert forbidden not in serialized_json

    delete_proposal = AgentPlanProposal(
        planning_result=_planning_result(),
        actions=(
            AgentProposedAction(
                action_key="delete_1",
                tool_name=AgentWriteToolName.DELETE_TASK,
                arguments={"task_id": str(task_id)},
            ),
        ),
    )
    deleted = AgentApprovalPreview(
        run_id=uuid4(),
        revision=0,
        proposal_fingerprint=fingerprint_plan_proposal(delete_proposal),
        planning_result=delete_proposal.planning_result,
        actions=(
            DeleteTaskApprovalPreview(
                action_key="delete_1",
                tool_name=AgentWriteToolName.DELETE_TASK,
                task_id=task_id,
            ),
        ),
    )
    assert deleted.to_plan_proposal() == delete_proposal


def test_approval_preview_enforces_exact_utf8_response_boundary() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000201")
    project_id = UUID("00000000-0000-0000-0000-000000000202")
    planning_result = PlanningResult(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        status=PlanningStatus.COMPLETED,
        plan=StudyPlan(
            summary="s" * 1_000,
            steps=tuple(
                StudyPlanStep(
                    step_key=f"step_{position}",
                    position=position,
                    title="t" * 200,
                    description="d" * 1_000,
                    success_criteria="c" * 500,
                )
                for position in range(1, 21)
            ),
        ),
    )

    def candidate(variable_length: int, *, validate: bool) -> AgentApprovalPreview:
        task_payloads: list[dict[str, JsonValue]] = [
            {
                "project_id": str(project_id),
                "title": f"{index}".ljust(300, "t"),
                "description": "x" * (2_200 if index < 9 else variable_length),
            }
            for index in range(10)
        ]
        proposal = AgentPlanProposal(
            planning_result=planning_result,
            actions=(
                AgentProposedAction(
                    action_key="batch_1",
                    tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
                    arguments={"tasks": cast(JsonValue, task_payloads)},
                ),
            ),
        )
        fingerprint = fingerprint_plan_proposal(proposal)
        actions = (
            BatchCreateTasksApprovalPreview(
                action_key="batch_1",
                tool_name=AgentWriteToolName.BATCH_CREATE_TASKS,
                tasks=tuple(
                    TaskCreate.model_validate(payload) for payload in task_payloads
                ),
            ),
        )
        if validate:
            return AgentApprovalPreview(
                run_id=run_id,
                revision=0,
                proposal_fingerprint=fingerprint,
                planning_result=planning_result,
                actions=actions,
            )
        return AgentApprovalPreview.model_construct(
            run_id=run_id,
            revision=0,
            proposal_fingerprint=fingerprint,
            planning_result=planning_result,
            actions=actions,
        )

    one_byte_length = len(candidate(1, validate=False).canonical_json().encode())
    exact_variable_length = MAX_APPROVAL_PREVIEW_BYTES - one_byte_length + 1
    assert 1 <= exact_variable_length < 5_000

    exact = candidate(exact_variable_length, validate=True)
    assert len(exact.canonical_json().encode()) == MAX_APPROVAL_PREVIEW_BYTES
    with pytest.raises(ValidationError, match=AGENT_APPROVAL_PREVIEW_INVALID_MESSAGE):
        candidate(exact_variable_length + 1, validate=True)


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_public_records_reject_naive_timestamps(field: str) -> None:
    payload = {
        "id": uuid4(),
        "goal_summary": "Goal",
        "status": "ACTIVE",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload[field] = datetime(2026, 9, 4)
    with pytest.raises(ValidationError):
        PublicAgentThread.model_validate(payload)


def test_public_records_normalize_aware_timestamps_to_utc() -> None:
    local = datetime(2026, 9, 4, 8, tzinfo=timezone(timedelta(hours=8)))
    public = PublicAgentThread(
        id=uuid4(),
        goal_summary="Goal",
        status=AgentThreadStatus.ACTIVE,
        created_at=local,
        updated_at=local,
    )
    assert public.created_at == datetime(2026, 9, 4, tzinfo=UTC)


def _approval_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": uuid4(),
        "run_id": uuid4(),
        "revision": 0,
        "proposal_fingerprint": "a" * 64,
        "decision": "PENDING",
        "feedback": None,
        "decided_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_approval_pending_and_decided_states_are_consistent() -> None:
    pending = PublicAgentApproval.model_validate(_approval_payload())
    assert pending.decision is AgentApprovalStatus.PENDING
    approved = PublicAgentApproval.model_validate(
        _approval_payload(decision="APPROVED", decided_at=NOW)
    )
    assert approved.decided_at == NOW
    changed = PublicAgentApproval.model_validate(
        _approval_payload(
            decision="REQUEST_CHANGES", decided_at=NOW, feedback="  Shorter  "
        )
    )
    assert changed.feedback == "Shorter"


@pytest.mark.parametrize(
    "overrides",
    [
        {"revision": -1},
        {"revision": 3},
        {"proposal_fingerprint": "bad"},
        {"decision": "PENDING", "decided_at": NOW},
        {"decision": "APPROVED", "decided_at": None},
        {"decision": "APPROVED", "decided_at": NOW, "feedback": "not allowed"},
        {"decision": "REQUEST_CHANGES", "decided_at": NOW, "feedback": None},
        {"decision": "UNKNOWN"},
        {"session": "forbidden"},
    ],
)
def test_approval_rejects_invalid_or_internal_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as error:
        PublicAgentApproval.model_validate(_approval_payload(**overrides))
    assert "forbidden-secret-value" not in str(error.value)


def test_public_approval_has_exact_safe_fields() -> None:
    public = PublicAgentApproval.model_validate(_approval_payload())
    assert set(public.model_dump()) == {
        "id",
        "run_id",
        "revision",
        "proposal_fingerprint",
        "decision",
        "feedback",
        "decided_at",
        "created_at",
        "updated_at",
    }
    serialized = public.model_dump_json().lower()
    for forbidden in (
        "user_id",
        "arguments",
        "reasoning",
        "raw_response",
        "api_key",
        "access_token",
        "authorization",
        "database_url",
        "session",
    ):
        assert forbidden not in serialized
