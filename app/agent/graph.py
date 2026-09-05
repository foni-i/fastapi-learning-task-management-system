"""Synchronous, bounded LangGraph composition for the Stage 9 Agent."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from langchain_core.runnables.graph import Graph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agent.context import AgentRuntimeContext
from app.agent.metrics import AgentRunMetrics
from app.agent.nodes.approval import (
    AgentApprovalInterrupt,
    AgentApprovalResponse,
    ApprovalDecider,
    interrupt_for_approval,
    request_approval,
)
from app.agent.nodes.context import analyze_goal, load_context
from app.agent.nodes.execution import IdempotentActionExecutor, execute_tasks
from app.agent.nodes.finalization import summarize, verify_result
from app.agent.nodes.planning import generate_plan, validate_plan
from app.agent.planning import Clock, Sleeper
from app.agent.providers import ModelProvider
from app.agent.routing import AgentRoute, route_after_approval
from app.agent.state import (
    AgentGraphInput,
    AgentGraphOutput,
    AgentGraphState,
    AgentTerminalStatus,
    AgentValidationStatus,
)
from app.agent.tools import AgentToolGateway

AGENT_GRAPH_RECURSION_LIMIT = 16
AGENT_GRAPH_FAILURE_SUMMARY = "Agent workflow failed safely."
AGENT_CHECKPOINT_THREAD_MESSAGE = "Agent checkpoint thread ID is required"

ANALYZE_GOAL = "analyze_goal"
LOAD_CONTEXT = "load_context"
GENERATE_PLAN = "generate_plan"
VALIDATE_PLAN = "validate_plan"
REQUEST_APPROVAL = "request_approval"
EXECUTE_TASKS = "execute_tasks"
VERIFY_RESULT = "verify_result"
SUMMARIZE = "summarize"

AGENT_GRAPH_NODE_NAMES = (
    ANALYZE_GOAL,
    LOAD_CONTEXT,
    GENERATE_PLAN,
    VALIDATE_PLAN,
    REQUEST_APPROVAL,
    EXECUTE_TASKS,
    VERIFY_RESULT,
    SUMMARIZE,
)

_ANALYSIS_FAILED = "AGENT_ANALYSIS_FAILED"
_CONTEXT_FAILED = "AGENT_CONTEXT_FAILED"
_PLANNING_FAILED = "AGENT_PLANNING_FAILED"
_APPROVAL_FAILED = "AGENT_APPROVAL_FAILED"
_EXECUTION_FAILED = "AGENT_EXECUTION_NODE_FAILED"

type StateUpdate = dict[str, object]


class _CompiledGraph(Protocol):
    def invoke(
        self,
        input: dict[str, object] | Command[object],
        config: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def get_graph(self) -> Graph: ...

    def get_state(self, config: dict[str, object]) -> object: ...


class AgentCheckpointThreadError(RuntimeError):
    """Reject missing or invalid host-owned checkpoint identity safely."""


@dataclass(frozen=True)
class AgentWorkflowProgress:
    """Return either one safe approval interrupt or one terminal output."""

    approval: AgentApprovalInterrupt | None = None
    output: AgentGraphOutput | None = None


def _failed(code: str) -> StateUpdate:
    return {"workflow_error_code": code}


def _sum_optional(first: int | None, second: int | None) -> int | None:
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)


def _merge_metrics(
    previous: AgentRunMetrics | None,
    current: AgentRunMetrics,
) -> AgentRunMetrics:
    if previous is None:
        return current
    return current.model_copy(
        update={
            "model_round_count": (
                previous.model_round_count + current.model_round_count
            ),
            "provider_attempt_count": (
                previous.provider_attempt_count + current.provider_attempt_count
            ),
            "tool_call_count": previous.tool_call_count + current.tool_call_count,
            "input_tokens": _sum_optional(
                previous.input_tokens,
                current.input_tokens,
            ),
            "output_tokens": _sum_optional(
                previous.output_tokens,
                current.output_tokens,
            ),
            "total_tokens": _sum_optional(
                previous.total_tokens,
                current.total_tokens,
            ),
            "latency_ms": previous.latency_ms + current.latency_ms,
        }
    )


class AgentWorkflow:
    """Hide LangGraph state and invocation controls behind a strict facade."""

    def __init__(
        self,
        compiled: _CompiledGraph,
        *,
        checkpointing_enabled: bool = False,
        durable_approval_enabled: bool = False,
    ) -> None:
        self._compiled = compiled
        self._checkpointing_enabled = checkpointing_enabled
        self._durable_approval_enabled = durable_approval_enabled

    def get_graph(self) -> Graph:
        """Expose static topology for inspection without exposing runtime state."""

        return self._compiled.get_graph()

    def invoke(
        self,
        graph_input: AgentGraphInput,
        *,
        thread_id: UUID | None = None,
    ) -> AgentGraphOutput:
        """Run with a host-owned recursion ceiling and return only public output."""

        validated_input = AgentGraphInput.model_validate(graph_input)
        if thread_id is not None and not isinstance(thread_id, UUID):
            raise AgentCheckpointThreadError(AGENT_CHECKPOINT_THREAD_MESSAGE)
        if self._checkpointing_enabled and thread_id is None:
            raise AgentCheckpointThreadError(AGENT_CHECKPOINT_THREAD_MESSAGE)
        config: dict[str, object] = {"recursion_limit": AGENT_GRAPH_RECURSION_LIMIT}
        if thread_id is not None:
            config["configurable"] = {"thread_id": str(thread_id)}
        try:
            result = self._compiled.invoke(
                validated_input.model_dump(mode="python"),
                config=config,
            )
            final_state = AgentGraphState.model_validate(result)
            return summarize(final_state)
        except GraphRecursionError:
            return AgentGraphOutput(
                status=AgentTerminalStatus.FAILED,
                summary=AGENT_GRAPH_FAILURE_SUMMARY,
            )
        except Exception:
            return AgentGraphOutput(
                status=AgentTerminalStatus.FAILED,
                summary=AGENT_GRAPH_FAILURE_SUMMARY,
            )

    def start_durable(
        self,
        graph_input: AgentGraphInput,
        *,
        thread_id: UUID,
    ) -> AgentWorkflowProgress:
        """Run a checkpointed workflow until approval or terminal completion."""

        if not self._durable_approval_enabled:
            raise AgentCheckpointThreadError(AGENT_CHECKPOINT_THREAD_MESSAGE)
        return self._advance(
            AgentGraphInput.model_validate(graph_input).model_dump(mode="python"),
            thread_id=thread_id,
        )

    def resume_durable(
        self,
        response: AgentApprovalResponse,
        *,
        thread_id: UUID,
    ) -> AgentWorkflowProgress:
        """Resume one durable approval using only a validated decision."""

        if not self._durable_approval_enabled:
            raise AgentCheckpointThreadError(AGENT_CHECKPOINT_THREAD_MESSAGE)
        return self._advance(
            Command(resume=response.model_dump(mode="json")),
            thread_id=thread_id,
        )

    def _advance(
        self,
        graph_input: dict[str, object] | Command[object],
        *,
        thread_id: UUID,
    ) -> AgentWorkflowProgress:
        config: dict[str, object] = {
            "recursion_limit": AGENT_GRAPH_RECURSION_LIMIT,
            "configurable": {"thread_id": str(thread_id)},
        }
        result = self._compiled.invoke(graph_input, config=config)
        snapshot = self._compiled.get_state(config)
        interrupts = getattr(snapshot, "interrupts", ())
        if interrupts:
            approval = AgentApprovalInterrupt.model_validate(interrupts[0].value)
            return AgentWorkflowProgress(approval=approval)
        state = AgentGraphState.model_validate(result)
        return AgentWorkflowProgress(output=summarize(state))


def build_agent_graph(
    *,
    model: str,
    provider: ModelProvider,
    gateway: AgentToolGateway,
    runtime_context: AgentRuntimeContext,
    approval_decider: ApprovalDecider,
    clock: Clock,
    sleeper: Sleeper,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    durable_approval: bool = False,
    action_executor: IdempotentActionExecutor | None = None,
) -> AgentWorkflow:
    """Compile the eight accepted nodes with host-owned runtime dependencies."""

    def analysis_node(state: AgentGraphState) -> StateUpdate:
        try:
            return analyze_goal(state)
        except Exception:
            return _failed(_ANALYSIS_FAILED)

    def context_node(state: AgentGraphState) -> StateUpdate:
        try:
            return load_context(
                state,
                runtime_context=runtime_context,
                gateway=gateway,
            )
        except Exception:
            return _failed(_CONTEXT_FAILED)

    def planning_node(state: AgentGraphState) -> StateUpdate:
        try:
            update = generate_plan(
                state,
                model=model,
                provider=provider,
                clock=clock,
                sleeper=sleeper,
            )
            update["metrics"] = _merge_metrics(state.metrics, update["metrics"])
            return dict(update)
        except Exception:
            return _failed(_PLANNING_FAILED)

    def validation_node(state: AgentGraphState) -> StateUpdate:
        try:
            return dict(validate_plan(state, runtime_context=runtime_context))
        except Exception:
            return _failed(_PLANNING_FAILED)

    if durable_approval and checkpointer is None:
        raise ValueError("Durable approval requires a checkpointer")

    def approval_node(state: AgentGraphState) -> StateUpdate:
        try:
            if durable_approval:
                return dict(interrupt_for_approval(state))
            return dict(request_approval(state, decider=approval_decider))
        except GraphInterrupt:
            raise
        except Exception:
            return _failed(_APPROVAL_FAILED)

    def execution_node(state: AgentGraphState) -> StateUpdate:
        try:
            update: StateUpdate = dict(
                execute_tasks(
                    state,
                    runtime_context=runtime_context,
                    gateway=gateway,
                    action_executor=action_executor,
                )
            )
            if state.metrics is not None:
                records = update["execution_records"]
                assert isinstance(records, tuple)
                update["metrics"] = state.metrics.model_copy(
                    update={
                        "tool_call_count": state.metrics.tool_call_count + len(records)
                    }
                )
            return update
        except Exception:
            return _failed(_EXECUTION_FAILED)

    def verification_node(state: AgentGraphState) -> StateUpdate:
        return dict(verify_result(state))

    def summary_node(state: AgentGraphState) -> StateUpdate:
        output = summarize(state)
        return {
            "terminal_status": output.status,
            "summary": output.summary,
        }

    def continue_or_verify(next_node: str) -> Callable[[AgentGraphState], str]:
        def route(state: AgentGraphState) -> str:
            return VERIFY_RESULT if state.workflow_error_code else next_node

        return route

    def route_validation(state: AgentGraphState) -> str:
        if state.workflow_error_code:
            return VERIFY_RESULT
        validation = state.validation
        if validation is None or validation.status is not AgentValidationStatus.VALID:
            return VERIFY_RESULT
        return REQUEST_APPROVAL

    def route_approval(state: AgentGraphState) -> str:
        if state.workflow_error_code:
            return VERIFY_RESULT
        route = route_after_approval(state)
        if route is AgentRoute.EXECUTE_TASKS:
            return EXECUTE_TASKS
        if route is AgentRoute.GENERATE_PLAN:
            return GENERATE_PLAN
        return VERIFY_RESULT

    builder = StateGraph(AgentGraphState, input_schema=AgentGraphInput)
    builder.add_node(ANALYZE_GOAL, analysis_node)
    builder.add_node(LOAD_CONTEXT, context_node)
    builder.add_node(GENERATE_PLAN, planning_node)
    builder.add_node(VALIDATE_PLAN, validation_node)
    builder.add_node(REQUEST_APPROVAL, approval_node)
    builder.add_node(EXECUTE_TASKS, execution_node)
    builder.add_node(VERIFY_RESULT, verification_node)
    builder.add_node(SUMMARIZE, summary_node)

    builder.add_edge(START, ANALYZE_GOAL)
    builder.add_conditional_edges(
        ANALYZE_GOAL,
        continue_or_verify(LOAD_CONTEXT),
        {LOAD_CONTEXT: LOAD_CONTEXT, VERIFY_RESULT: VERIFY_RESULT},
    )
    builder.add_conditional_edges(
        LOAD_CONTEXT,
        continue_or_verify(GENERATE_PLAN),
        {GENERATE_PLAN: GENERATE_PLAN, VERIFY_RESULT: VERIFY_RESULT},
    )
    builder.add_conditional_edges(
        GENERATE_PLAN,
        continue_or_verify(VALIDATE_PLAN),
        {VALIDATE_PLAN: VALIDATE_PLAN, VERIFY_RESULT: VERIFY_RESULT},
    )
    builder.add_conditional_edges(
        VALIDATE_PLAN,
        route_validation,
        {REQUEST_APPROVAL: REQUEST_APPROVAL, VERIFY_RESULT: VERIFY_RESULT},
    )
    builder.add_conditional_edges(
        REQUEST_APPROVAL,
        route_approval,
        {
            EXECUTE_TASKS: EXECUTE_TASKS,
            GENERATE_PLAN: GENERATE_PLAN,
            VERIFY_RESULT: VERIFY_RESULT,
        },
    )
    builder.add_edge(EXECUTE_TASKS, VERIFY_RESULT)
    builder.add_edge(VERIFY_RESULT, SUMMARIZE)
    builder.add_edge(SUMMARIZE, END)
    compiled = cast(_CompiledGraph, builder.compile(checkpointer=checkpointer))
    return AgentWorkflow(
        compiled,
        checkpointing_enabled=checkpointer is not None,
        durable_approval_enabled=durable_approval,
    )
