"""Strict public contracts for Agent business records."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.agent_run import AgentApprovalStatus, AgentRunStatus, AgentThreadStatus
from app.schemas.agent_run import (
    AgentRunMetricsSnapshot,
    AgentThreadCreate,
    AgentThreadRunResult,
    PublicAgentApproval,
    PublicAgentRun,
    PublicAgentThread,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


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
