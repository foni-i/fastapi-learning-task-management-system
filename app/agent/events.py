"""Transport-neutral safe progress events for the bounded Agent loop."""

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.context import AgentRuntimeContext
from app.agent.loop import MODEL_TIMEOUT_SECONDS, AgentLoopMachine
from app.agent.metrics import AgentRunMetrics, AgentRunOutcome
from app.agent.providers import ProviderResponse, ProviderUsage, StreamingModelProvider
from app.agent.schemas import PlanningGoal, PlanningResult
from app.agent.tools import AgentToolGateway

EVENT_SUMMARY_MAX_LENGTH = 300


class AgentEventKind(StrEnum):
    RUN_STARTED = "run_started"
    MODEL_STARTED = "model_started"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    RESULT_READY = "result_ready"
    RUN_FAILED = "run_failed"


class AgentEventOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentProgressEvent(BaseModel):
    """Expose ordered progress without raw provider or tool payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    sequence: int = Field(ge=1)
    timestamp: datetime
    kind: AgentEventKind
    stage: str = Field(default="planning", pattern=r"^planning$")
    tool_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    summary: str = Field(min_length=1, max_length=EVENT_SUMMARY_MAX_LENGTH)
    outcome: AgentEventOutcome | None = None
    usage: ProviderUsage | None = None
    result: PlanningResult | None = None
    metrics: AgentRunMetrics | None = None

    @field_validator("timestamp")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Agent event timestamp must include timezone information")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        tool_event = self.kind in {
            AgentEventKind.TOOL_STARTED,
            AgentEventKind.TOOL_FINISHED,
        }
        if tool_event != (self.tool_name is not None):
            raise ValueError("Agent tool events require exactly one tool name")
        if (self.kind is AgentEventKind.RESULT_READY) != (self.result is not None):
            raise ValueError("Only a result-ready event may contain a result")
        if (
            self.kind is AgentEventKind.RESULT_READY
            and self.outcome is not AgentEventOutcome.SUCCEEDED
        ):
            raise ValueError("Result-ready event must report success")
        if (
            self.kind is AgentEventKind.RUN_FAILED
            and self.outcome is not AgentEventOutcome.FAILED
        ):
            raise ValueError("Run-failed event must report failure")
        if self.kind is AgentEventKind.RESULT_READY and self.metrics is None:
            raise ValueError("Result-ready events require metrics")
        if (
            self.kind
            not in {
                AgentEventKind.RESULT_READY,
                AgentEventKind.RUN_FAILED,
            }
            and self.metrics is not None
        ):
            raise ValueError("Only terminal Agent events may contain metrics")
        return self


EventClock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _transition_observer(
    transitions: list[tuple[AgentEventKind, str]],
    kind: AgentEventKind,
) -> Callable[[str], None]:
    def observe(tool_name: str) -> None:
        transitions.append((kind, tool_name))

    return observe


def stream_agent(
    goal: PlanningGoal | Mapping[str, object],
    *,
    context: AgentRuntimeContext,
    model: str,
    provider: StreamingModelProvider,
    gateway: AgentToolGateway,
    clock: EventClock = utc_now,
    metric_clock: Callable[[], float] = monotonic,
) -> Iterator[AgentProgressEvent]:
    """Yield safe progress while driving the shared bounded loop machine."""

    sequence = 0

    def event(
        kind: AgentEventKind,
        summary: str,
        *,
        tool_name: str | None = None,
        outcome: AgentEventOutcome | None = None,
        usage: ProviderUsage | None = None,
        result: PlanningResult | None = None,
        metrics: AgentRunMetrics | None = None,
    ) -> AgentProgressEvent:
        nonlocal sequence
        sequence += 1
        return AgentProgressEvent(
            sequence=sequence,
            timestamp=clock(),
            kind=kind,
            tool_name=tool_name,
            summary=summary,
            outcome=outcome,
            usage=usage,
            result=result,
            metrics=metrics,
        )

    yield event(AgentEventKind.RUN_STARTED, "Study planning started")
    machine: AgentLoopMachine | None = None
    try:
        machine = AgentLoopMachine(
            goal,
            context=context,
            model=model,
            gateway=gateway,
            clock=metric_clock,
        )
        while True:
            request = machine.next_request()
            yield event(AgentEventKind.MODEL_STARTED, "Model generation started")
            final_response: ProviderResponse | None = None
            text_parts: list[str] = []
            with provider.stream(
                request,
                timeout_seconds=MODEL_TIMEOUT_SECONDS,
            ) as chunks:
                for chunk in chunks:
                    if chunk.output_text_delta is not None:
                        text_parts.append(chunk.output_text_delta)
                    else:
                        final_response = chunk.response
            if final_response is None:
                raise RuntimeError("provider stream ended without a response")
            if text_parts and final_response.output_text != "".join(text_parts):
                raise RuntimeError("provider stream output was inconsistent")

            transitions: list[tuple[AgentEventKind, str]] = []
            execution = machine.accept_response(
                final_response,
                on_tool_started=_transition_observer(
                    transitions,
                    AgentEventKind.TOOL_STARTED,
                ),
                on_tool_finished=_transition_observer(
                    transitions,
                    AgentEventKind.TOOL_FINISHED,
                ),
            )
            for kind, tool_name in transitions:
                yield event(
                    kind,
                    "Tool execution started"
                    if kind is AgentEventKind.TOOL_STARTED
                    else "Tool execution finished",
                    tool_name=tool_name,
                    outcome=(
                        AgentEventOutcome.SUCCEEDED
                        if kind is AgentEventKind.TOOL_FINISHED
                        else None
                    ),
                )
            if execution is not None:
                yield event(
                    AgentEventKind.RESULT_READY,
                    "Study plan is ready",
                    outcome=AgentEventOutcome.SUCCEEDED,
                    usage=final_response.usage,
                    result=execution.result,
                    metrics=execution.metrics,
                )
                return
    except GeneratorExit:
        raise
    except Exception:
        yield event(
            AgentEventKind.RUN_FAILED,
            "Study planning failed",
            outcome=AgentEventOutcome.FAILED,
            metrics=(
                machine.metrics(AgentRunOutcome.FAILED) if machine is not None else None
            ),
        )
