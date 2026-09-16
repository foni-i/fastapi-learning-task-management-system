"""Deterministic goal analysis and bounded owner-scoped context loading."""

from collections.abc import Callable
from time import monotonic
from typing import Protocol
from uuid import UUID

from app.agent.context import AgentRuntimeContext
from app.agent.grounding import build_grounded_knowledge
from app.agent.state import (
    AgentContextKind,
    AgentContextSnapshot,
    AgentGoalAnalysis,
    AgentGraphState,
)
from app.agent.tools import AgentToolGateway, AgentToolResult, execute_tool
from app.agent.tracing import (
    NOOP_TRACE_SINK,
    TraceComponent,
    TraceErrorCode,
    TraceSink,
    start_trace,
)
from app.schemas.knowledge_retrieval import KnowledgeSearchResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import TaskListResponse

AGENT_CONTEXT_UNAVAILABLE_MESSAGE = "Agent context could not be loaded"
CONTEXT_PAGE = 1
CONTEXT_PAGE_SIZE = 20
KNOWLEDGE_TOP_K = 10
TraceClock = Callable[[], float]


class AgentContextUnavailableError(Exception):
    """Hide context payloads and lower-layer diagnostics from workflow state."""


class ToolExecutor(Protocol):
    def __call__(
        self,
        name: str,
        arguments: dict[str, object],
        context: AgentRuntimeContext,
        *,
        gateway: AgentToolGateway | None = None,
    ) -> AgentToolResult: ...


def analyze_goal(state: AgentGraphState) -> dict[str, object]:
    """Return only transparent deterministic facts derived from the goal."""

    analysis = AgentGoalAnalysis(
        objective=state.goal.objective,
        constraints=state.goal.constraints,
        required_context=(
            AgentContextKind.PROJECTS,
            AgentContextKind.TASKS,
            AgentContextKind.KNOWLEDGE,
        ),
    )
    return {"analysis": analysis}


def load_context(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
    executor: ToolExecutor = execute_tool,
    gateway: AgentToolGateway | None = None,
    trace_sink: TraceSink = NOOP_TRACE_SINK,
    trace_run_id: UUID | None = None,
    trace_clock: TraceClock = monotonic,
) -> dict[str, object]:
    """Load a fixed first page through read tools and no lower-layer shortcut."""

    expected = (
        AgentContextKind.PROJECTS,
        AgentContextKind.TASKS,
        AgentContextKind.KNOWLEDGE,
    )
    if state.analysis is None or state.analysis.required_context != expected:
        raise AgentContextUnavailableError(AGENT_CONTEXT_UNAVAILABLE_MESSAGE)

    read_only_context = AgentRuntimeContext(user_id=runtime_context.user_id)

    def invoke_tool(name: str, arguments: dict[str, object]) -> AgentToolResult:
        span = start_trace(
            trace_sink,
            run_id=trace_run_id,
            component=TraceComponent.TOOL,
            name=name,
            clock=trace_clock,
        )
        try:
            result = executor(
                name,
                arguments,
                read_only_context,
                gateway=gateway,
            )
        except Exception:
            span.fail(TraceErrorCode.TOOL_EXECUTION_FAILED)
            raise
        span.finish()
        return result

    try:
        projects = invoke_tool(
            "list_projects",
            {
                "page": CONTEXT_PAGE,
                "page_size": CONTEXT_PAGE_SIZE,
                "include_archived": False,
            },
        )
        tasks = invoke_tool(
            "list_tasks",
            {"page": CONTEXT_PAGE, "page_size": CONTEXT_PAGE_SIZE},
        )
        knowledge = invoke_tool(
            "search_knowledge",
            {"query": state.analysis.objective, "top_k": KNOWLEDGE_TOP_K},
        )
        if (
            not isinstance(projects, ProjectListResponse)
            or not isinstance(tasks, TaskListResponse)
            or not isinstance(knowledge, KnowledgeSearchResult)
        ):
            raise TypeError("unexpected tool result")
        snapshot = AgentContextSnapshot(
            projects=projects,
            tasks=tasks,
            knowledge=build_grounded_knowledge(knowledge),
        )
    except Exception:
        raise AgentContextUnavailableError(AGENT_CONTEXT_UNAVAILABLE_MESSAGE) from None
    return {"context": snapshot}
