"""Offline tests for safe Agent metrics and reusable provider fakes."""

import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.events import AgentEventKind, stream_agent
from app.agent.metrics import (
    AgentRunMetrics,
    AgentRunOutcome,
    aggregate_usage,
    build_run_metrics,
)
from app.agent.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from app.agent.testing import run_agent_for_test
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from tests.fakes.agent_provider import ScriptedAgentProvider, ScriptedStream

FINAL_RESULT = """{"prompt_version":"study-plan.v1","status":"completed","plan":{"summary":"Safe plan","steps":[{"step_key":"step_1","position":1,"title":"Study","description":"Read","success_criteria":"Notes exist"}]}}"""


class EmptyGateway:
    def list_projects(
        self, *, user_id: UUID, page: int, page_size: int, include_archived: bool
    ) -> ProjectListResponse:
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(self, *, user_id: UUID, query: TaskListQuery) -> TaskListResponse:
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(self, *, user_id: UUID, task_input: TaskCreate) -> PublicTask:
        raise AssertionError("not used")

    def update_task(
        self, *, user_id: UUID, task_id: UUID, task_update: TaskUpdate
    ) -> PublicTask:
        raise AssertionError("not used")


def clock(values: list[float]) -> Iterator[float]:
    yield from values


def test_usage_aggregation_preserves_missing_and_sums_known_values() -> None:
    assert aggregate_usage([ProviderUsage(), ProviderUsage()]) == ProviderUsage()
    assert aggregate_usage(
        [
            ProviderUsage(input_tokens=2, output_tokens=None, total_tokens=5),
            ProviderUsage(input_tokens=3, output_tokens=7, total_tokens=None),
        ]
    ) == ProviderUsage(input_tokens=5, output_tokens=7, total_tokens=5)


def test_metrics_are_exact_immutable_serializable_and_redacted() -> None:
    metrics = build_run_metrics(
        prompt_version="study-plan.v1",
        outcome=AgentRunOutcome.SUCCEEDED,
        model_round_count=2,
        provider_attempt_count=2,
        tool_call_count=1,
        usages=[ProviderUsage(input_tokens=10, output_tokens=4, total_tokens=14)],
        started_at=4.0,
        finished_at=4.125,
    )
    assert metrics.latency_ms == 125
    assert AgentRunMetrics.model_validate_json(metrics.model_dump_json()) == metrics
    assert set(metrics.model_dump()) == {
        "prompt_version",
        "outcome",
        "model_round_count",
        "provider_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }
    for forbidden in ("instructions", "arguments", "user_id", "api_key"):
        assert forbidden not in metrics.model_dump_json().casefold()
    with pytest.raises(ValidationError):
        metrics.latency_ms = 0


def test_metrics_reject_non_monotonic_clock() -> None:
    with pytest.raises(ValueError, match="monotonic"):
        build_run_metrics(
            prompt_version="study-plan.v1",
            outcome=AgentRunOutcome.FAILED,
            model_round_count=1,
            provider_attempt_count=1,
            tool_call_count=0,
            usages=[],
            started_at=2.0,
            finished_at=1.0,
        )


def test_loop_reports_round_tool_usage_and_fake_records_only_safe_metadata() -> None:
    provider = ScriptedAgentProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(call_id="call_1", name="list_tasks", arguments={}),
                ),
                usage=ProviderUsage(input_tokens=10, output_tokens=2, total_tokens=12),
            ),
            ProviderResponse(
                output_text=FINAL_RESULT,
                usage=ProviderUsage(input_tokens=20, output_tokens=8, total_tokens=28),
            ),
        ]
    )
    ticks = clock([10.0, 10.25])
    execution = run_agent_for_test(
        {"objective": "Plan a study session"},
        context=AgentRuntimeContext(user_id=uuid4()),
        model="synthetic-model",
        provider=provider,
        gateway=EmptyGateway(),
        clock=lambda: next(ticks),
    )
    assert execution.metrics == AgentRunMetrics(
        prompt_version="study-plan.v1",
        outcome=AgentRunOutcome.SUCCEEDED,
        model_round_count=2,
        provider_attempt_count=2,
        tool_call_count=1,
        input_tokens=30,
        output_tokens=10,
        total_tokens=40,
        latency_ms=250,
    )
    assert len(provider.calls) == 2
    assert set(provider.calls[0].__dict__) == {
        "prompt_version",
        "tool_names",
        "timeout_seconds",
    }


def test_stream_terminal_events_include_success_and_failure_metrics() -> None:
    success_provider = ScriptedAgentProvider(
        [
            ScriptedStream(
                response=ProviderResponse(output_text=FINAL_RESULT),
                deltas=(FINAL_RESULT,),
            )
        ]
    )
    success_ticks = clock([1.0, 1.1])
    success_events = list(
        stream_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="synthetic-model",
            provider=success_provider,
            gateway=EmptyGateway(),
            clock=lambda: datetime(2026, 9, 3, tzinfo=UTC),
            metric_clock=lambda: next(success_ticks),
        )
    )
    assert success_events[-1].kind is AgentEventKind.RESULT_READY
    assert success_events[-1].metrics is not None
    assert success_events[-1].metrics.outcome is AgentRunOutcome.SUCCEEDED
    assert success_provider.closed_streams == 1

    failed_provider = ScriptedAgentProvider([RuntimeError("sensitive diagnostic")])
    failed_ticks = clock([2.0, 2.1])
    failed_events = list(
        stream_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="synthetic-model",
            provider=failed_provider,
            gateway=EmptyGateway(),
            metric_clock=lambda: next(failed_ticks),
        )
    )
    assert failed_events[-1].kind is AgentEventKind.RUN_FAILED
    assert failed_events[-1].metrics is not None
    assert failed_events[-1].metrics.outcome is AgentRunOutcome.FAILED
    assert "sensitive diagnostic" not in failed_events[-1].model_dump_json()


def test_external_provider_marker_is_excluded_from_default_pytest() -> None:
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = configuration["tool"]["pytest"]["ini_options"]

    assert "not external_provider" in pytest_options["addopts"]
    assert any(
        marker.startswith("external_provider:") for marker in pytest_options["markers"]
    )


def test_external_provider_smoke_skips_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.external.test_agent_provider_smoke import (
        test_openai_provider_returns_tiny_structured_response,
    )

    monkeypatch.delenv("STMS_RUN_EXTERNAL_PROVIDER_SMOKE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        test_openai_provider_returns_tiny_structured_response()
