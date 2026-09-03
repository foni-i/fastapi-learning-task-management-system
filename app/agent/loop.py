"""Deterministic bounded tool-calling loop for internal Stage 8 use."""

import json
from collections.abc import Callable, Mapping
from enum import StrEnum
from time import monotonic
from typing import Never

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.metrics import AgentRunMetrics, AgentRunOutcome, build_run_metrics
from app.agent.prompts import build_study_plan_prompt
from app.agent.providers import (
    ModelProvider,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderPermissionError,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from app.agent.schemas import PlanningGoal, PlanningResult
from app.agent.tools import (
    AgentToolGateway,
    AgentToolResult,
    available_tool_definitions,
    execute_tool,
    validate_tool_arguments,
)
from app.core.exceptions import (
    AGENT_PLANNING_CONFIGURATION_MESSAGE,
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningConfigurationError,
    AgentPlanningUnavailableError,
)

MAX_MODEL_ROUNDS = 4
MAX_TOOL_CALLS_PER_RESPONSE = 3
MAX_TOTAL_TOOL_CALLS = 8
MAX_SINGLE_TOOL_RESULT_CHARS = 6_000
MAX_ACCUMULATED_TOOL_RESULT_CHARS = 12_000
MODEL_TIMEOUT_SECONDS = 30.0

ToolDispatcher = Callable[..., AgentToolResult]
ToolValidator = Callable[..., object]
ToolObserver = Callable[[str], None]
MonotonicClock = Callable[[], float]


class ToolExecutionOutcome(StrEnum):
    SUCCEEDED = "succeeded"


class AgentToolExecutionRecord(BaseModel):
    """Record safe execution metadata without arguments or result payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    outcome: ToolExecutionOutcome


class AgentRunTrace(BaseModel):
    """Expose only bounded serializable metadata to the internal test entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_version: str
    round_count: int = Field(ge=1, le=MAX_MODEL_ROUNDS)
    tool_count: int = Field(ge=0, le=MAX_TOTAL_TOOL_CALLS)
    tool_records: tuple[AgentToolExecutionRecord, ...]


class AgentLoopExecution(BaseModel):
    """Pair the final strict plan with safe test-visible execution metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: PlanningResult
    trace: AgentRunTrace
    metrics: AgentRunMetrics


def _fail_unavailable() -> Never:
    raise AgentPlanningUnavailableError(AGENT_PLANNING_UNAVAILABLE_MESSAGE) from None


def _provider_request(
    goal: PlanningGoal,
    *,
    model: str,
    context: AgentRuntimeContext,
    feedback: list[dict[str, object]],
) -> ProviderRequest:
    prompt = build_study_plan_prompt(goal)
    provider_input = prompt.input
    if feedback:
        serialized = json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
        provider_input = f"{provider_input}\nValidated tool result data:\n{serialized}"
    if len(provider_input) > 20_000:
        _fail_unavailable()
    return ProviderRequest(
        model=model,
        prompt_version=prompt.version,
        instructions=prompt.instructions,
        input=provider_input,
        output_schema_name="PlanningResult",
        output_schema=PlanningResult.model_json_schema(),
        tools=available_tool_definitions(context),
    )


class AgentLoopMachine:
    """Hold the single bounded state machine shared by sync and stream drivers."""

    def __init__(
        self,
        goal: PlanningGoal | Mapping[str, object],
        *,
        context: AgentRuntimeContext,
        model: str,
        gateway: AgentToolGateway,
        dispatcher: ToolDispatcher = execute_tool,
        validator: ToolValidator = validate_tool_arguments,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self.goal = PlanningGoal.model_validate(goal)
        self.context = context
        self.model = model
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.validator = validator
        self.clock = clock
        self.started_at = clock()
        self.feedback: list[dict[str, object]] = []
        self.feedback_chars = 0
        self.seen_call_ids: set[str] = set()
        self.records: list[AgentToolExecutionRecord] = []
        self.round_count = 0
        self.usages: list[ProviderUsage] = []

    def metrics(self, outcome: AgentRunOutcome) -> AgentRunMetrics:
        """Snapshot safe aggregate metrics without retaining payloads."""

        return build_run_metrics(
            prompt_version=build_study_plan_prompt(self.goal).version,
            outcome=outcome,
            model_round_count=self.round_count,
            provider_attempt_count=self.round_count,
            tool_call_count=len(self.records),
            usages=self.usages,
            started_at=self.started_at,
            finished_at=self.clock(),
        )

    def next_request(self) -> ProviderRequest:
        """Advance one model round or terminate at the fixed ceiling."""

        if self.round_count >= MAX_MODEL_ROUNDS:
            _fail_unavailable()
        self.round_count += 1
        return _provider_request(
            self.goal,
            model=self.model,
            context=self.context,
            feedback=self.feedback,
        )

    def accept_response(
        self,
        response: ProviderResponse,
        *,
        on_tool_started: ToolObserver | None = None,
        on_tool_finished: ToolObserver | None = None,
    ) -> AgentLoopExecution | None:
        """Validate one response, execute its batch, or return the final result."""

        self.usages.append(response.usage)
        if response.output_text is not None and response.tool_calls:
            _fail_unavailable()
        if response.output_text is not None:
            try:
                result = PlanningResult.model_validate_json(response.output_text)
            except ValidationError:
                _fail_unavailable()
            trace = AgentRunTrace(
                prompt_version=result.prompt_version,
                round_count=self.round_count,
                tool_count=len(self.records),
                tool_records=tuple(self.records),
            )
            return AgentLoopExecution(
                result=result,
                trace=trace,
                metrics=self.metrics(AgentRunOutcome.SUCCEEDED),
            )

        calls = response.tool_calls
        if not calls or len(calls) > MAX_TOOL_CALLS_PER_RESPONSE:
            _fail_unavailable()
        if len(self.records) + len(calls) > MAX_TOTAL_TOOL_CALLS:
            _fail_unavailable()
        call_ids = [call.call_id for call in calls]
        if len(call_ids) != len(set(call_ids)) or self.seen_call_ids.intersection(
            call_ids
        ):
            _fail_unavailable()

        try:
            for call in calls:
                self.validator(call.name, call.arguments, self.context)
        except Exception:
            _fail_unavailable()

        self.seen_call_ids.update(call_ids)
        for call in calls:
            if on_tool_started is not None:
                on_tool_started(call.name)
            try:
                tool_result = self.dispatcher(
                    call.name,
                    call.arguments,
                    self.context,
                    gateway=self.gateway,
                )
            except Exception:
                _fail_unavailable()
            serialized_result = tool_result.model_dump_json()
            if len(serialized_result) > MAX_SINGLE_TOOL_RESULT_CHARS:
                _fail_unavailable()
            self.feedback_chars += len(serialized_result)
            if self.feedback_chars > MAX_ACCUMULATED_TOOL_RESULT_CHARS:
                _fail_unavailable()
            self.feedback.append(
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "output": json.loads(serialized_result),
                }
            )
            self.records.append(
                AgentToolExecutionRecord(
                    call_id=call.call_id,
                    tool_name=call.name,
                    outcome=ToolExecutionOutcome.SUCCEEDED,
                )
            )
            if on_tool_finished is not None:
                on_tool_finished(call.name)
        return None


def _generate_safely(
    provider: ModelProvider,
    request: ProviderRequest,
) -> ProviderResponse:
    try:
        return provider.generate(request, timeout_seconds=MODEL_TIMEOUT_SECONDS)
    except (
        ProviderConfigurationError,
        ProviderAuthenticationError,
        ProviderPermissionError,
    ):
        raise AgentPlanningConfigurationError(
            AGENT_PLANNING_CONFIGURATION_MESSAGE
        ) from None
    except ProviderError:
        _fail_unavailable()
    except Exception:
        _fail_unavailable()


def _run_agent(
    goal: PlanningGoal | Mapping[str, object],
    *,
    context: AgentRuntimeContext,
    model: str,
    provider: ModelProvider,
    gateway: AgentToolGateway,
    dispatcher: ToolDispatcher = execute_tool,
    validator: ToolValidator = validate_tool_arguments,
    clock: MonotonicClock = monotonic,
) -> AgentLoopExecution:
    machine = AgentLoopMachine(
        goal,
        context=context,
        model=model,
        gateway=gateway,
        dispatcher=dispatcher,
        validator=validator,
        clock=clock,
    )
    while True:
        request = machine.next_request()
        response = _generate_safely(provider, request)
        execution = machine.accept_response(response)
        if execution is not None:
            return execution


def run_agent(
    goal: PlanningGoal | Mapping[str, object],
    *,
    context: AgentRuntimeContext,
    model: str,
    provider: ModelProvider,
    gateway: AgentToolGateway,
) -> PlanningResult:
    """Return one strict final plan while keeping trace data internal."""

    return _run_agent(
        goal,
        context=context,
        model=model,
        provider=provider,
        gateway=gateway,
    ).result
