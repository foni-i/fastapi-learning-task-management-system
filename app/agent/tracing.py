"""Strict content-free tracing contracts for bounded Agent observability."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRACE_SCHEMA_VERSION: Literal["agent-trace.v1"] = "agent-trace.v1"
MAX_TRACE_ATTEMPTS = 16
MAX_TRACE_TOOL_CALLS = 100
MAX_TRACE_TOKENS = 1_000_000_000
MAX_TRACE_LATENCY_MS = 86_400_000.0


class TraceComponent(StrEnum):
    RUN = "run"
    NODE = "node"
    TOOL = "tool"
    PROVIDER = "provider"


class TracePhase(StrEnum):
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"


class TraceOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TraceErrorCode(StrEnum):
    """Allow only stable classifications, never exception messages."""

    AGENT_ANALYSIS_FAILED = "AGENT_ANALYSIS_FAILED"
    AGENT_CONTEXT_FAILED = "AGENT_CONTEXT_FAILED"
    AGENT_PLANNING_FAILED = "AGENT_PLANNING_FAILED"
    AGENT_APPROVAL_FAILED = "AGENT_APPROVAL_FAILED"
    AGENT_EXECUTION_NODE_FAILED = "AGENT_EXECUTION_NODE_FAILED"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    AGENT_WORKFLOW_FAILED = "AGENT_WORKFLOW_FAILED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_CONFIGURATION = "PROVIDER_CONFIGURATION"
    PROVIDER_AUTHENTICATION = "PROVIDER_AUTHENTICATION"
    PROVIDER_PERMISSION = "PROVIDER_PERMISSION"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_EXECUTION_UNKNOWN = "TOOL_EXECUTION_UNKNOWN"


_COMPONENT_NAMES = {
    TraceComponent.RUN: frozenset({"agent_workflow"}),
    TraceComponent.NODE: frozenset(
        {
            "analyze_goal",
            "load_context",
            "generate_plan",
            "validate_plan",
            "request_approval",
            "execute_tasks",
            "verify_result",
            "summarize",
        }
    ),
    TraceComponent.TOOL: frozenset(
        {
            "list_projects",
            "list_tasks",
            "search_knowledge",
            "create_task",
            "update_task",
            "batch_create_tasks",
            "delete_task",
        }
    ),
    TraceComponent.PROVIDER: frozenset({"model_provider"}),
}


class AgentTraceEvent(BaseModel):
    """One allowlisted operational event with no content-bearing payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal["agent-trace.v1"] = TRACE_SCHEMA_VERSION
    run_id: UUID
    thread_id: UUID | None = None
    component: TraceComponent
    name: str = Field(min_length=1, max_length=64)
    phase: TracePhase
    outcome: TraceOutcome | None = None
    error_code: TraceErrorCode | None = None
    prompt_version: str | None = Field(
        default=None,
        pattern=r"^study-plan\.v[1-9][0-9]*$",
        max_length=100,
    )
    attempt: int | None = Field(default=None, ge=1, le=MAX_TRACE_ATTEMPTS)
    model_round_count: int | None = Field(default=None, ge=0, le=MAX_TRACE_ATTEMPTS)
    provider_attempt_count: int | None = Field(
        default=None, ge=0, le=MAX_TRACE_ATTEMPTS
    )
    tool_call_count: int | None = Field(default=None, ge=0, le=MAX_TRACE_TOOL_CALLS)
    input_tokens: int | None = Field(default=None, ge=0, le=MAX_TRACE_TOKENS)
    output_tokens: int | None = Field(default=None, ge=0, le=MAX_TRACE_TOKENS)
    total_tokens: int | None = Field(default=None, ge=0, le=MAX_TRACE_TOKENS)
    latency_ms: float | None = Field(default=None, ge=0, le=MAX_TRACE_LATENCY_MS)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.name not in _COMPONENT_NAMES[self.component]:
            raise ValueError("Trace component name is not allowlisted")
        if self.phase is TracePhase.STARTED:
            if (
                self.outcome is not None
                or self.error_code is not None
                or self.latency_ms is not None
            ):
                raise ValueError("Started traces cannot report a terminal result")
        elif self.phase is TracePhase.FINISHED:
            if (
                self.outcome is not TraceOutcome.SUCCEEDED
                or self.error_code is not None
                or self.latency_ms is None
            ):
                raise ValueError("Finished traces require a successful result")
        elif (
            self.outcome is not TraceOutcome.FAILED
            or self.error_code is None
            or self.latency_ms is None
        ):
            raise ValueError("Failed traces require a safe error classification")
        return self


class TraceSink(Protocol):
    """Synchronous metadata-only destination owned outside business state."""

    def emit(self, event: AgentTraceEvent) -> None: ...


class NoOpTraceSink:
    """Default sink that intentionally retains and exports nothing."""

    def emit(self, event: AgentTraceEvent) -> None:
        del event


NOOP_TRACE_SINK: TraceSink = NoOpTraceSink()


def emit_trace(sink: TraceSink, event: AgentTraceEvent) -> None:
    """Isolate observability failures from every business control path."""

    try:
        sink.emit(event)
    except Exception:
        return


TraceClock = Callable[[], float]


@dataclass(frozen=True)
class TraceSpan:
    """Keep timing mechanics outside graph, Tool, and Provider control flow."""

    sink: TraceSink
    run_id: UUID | None
    thread_id: UUID | None
    component: TraceComponent
    name: str
    prompt_version: str | None
    attempt: int | None
    started_at: float | None
    clock: TraceClock

    def finish(
        self,
        *,
        model_round_count: int | None = None,
        provider_attempt_count: int | None = None,
        tool_call_count: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self._terminal(
            phase=TracePhase.FINISHED,
            outcome=TraceOutcome.SUCCEEDED,
            model_round_count=model_round_count,
            provider_attempt_count=provider_attempt_count,
            tool_call_count=tool_call_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def fail(
        self,
        error_code: TraceErrorCode,
        *,
        model_round_count: int | None = None,
        provider_attempt_count: int | None = None,
        tool_call_count: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self._terminal(
            phase=TracePhase.FAILED,
            outcome=TraceOutcome.FAILED,
            error_code=error_code,
            model_round_count=model_round_count,
            provider_attempt_count=provider_attempt_count,
            tool_call_count=tool_call_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def _terminal(
        self,
        *,
        phase: TracePhase,
        outcome: TraceOutcome,
        error_code: TraceErrorCode | None = None,
        model_round_count: int | None = None,
        provider_attempt_count: int | None = None,
        tool_call_count: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if self.run_id is None or self.started_at is None:
            return
        try:
            emit_trace(
                self.sink,
                AgentTraceEvent(
                    run_id=self.run_id,
                    thread_id=self.thread_id,
                    component=self.component,
                    name=self.name,
                    phase=phase,
                    outcome=outcome,
                    error_code=error_code,
                    prompt_version=self.prompt_version,
                    attempt=self.attempt,
                    model_round_count=model_round_count,
                    provider_attempt_count=provider_attempt_count,
                    tool_call_count=tool_call_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=max(0.0, (self.clock() - self.started_at) * 1000),
                ),
            )
        except Exception:
            return


def start_trace(
    sink: TraceSink,
    *,
    run_id: UUID | None,
    component: TraceComponent,
    name: str,
    thread_id: UUID | None = None,
    prompt_version: str | None = None,
    attempt: int | None = None,
    clock: TraceClock = monotonic,
) -> TraceSpan:
    """Start one safe span, or return an inert span if tracing itself fails."""

    started_at: float | None = None
    if run_id is not None:
        try:
            started_at = clock()
            emit_trace(
                sink,
                AgentTraceEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    component=component,
                    name=name,
                    phase=TracePhase.STARTED,
                    prompt_version=prompt_version,
                    attempt=attempt,
                ),
            )
        except Exception:
            started_at = None
    return TraceSpan(
        sink=sink,
        run_id=run_id,
        thread_id=thread_id,
        component=component,
        name=name,
        prompt_version=prompt_version,
        attempt=attempt,
        started_at=started_at,
        clock=clock,
    )
