"""Deterministic goal analysis and bounded owner-scoped context loading."""

from typing import Protocol

from app.agent.context import AgentRuntimeContext
from app.agent.state import (
    AgentContextKind,
    AgentContextSnapshot,
    AgentGoalAnalysis,
    AgentGraphState,
)
from app.agent.tools import AgentToolGateway, AgentToolResult, execute_tool
from app.schemas.project import ProjectListResponse
from app.schemas.task import TaskListResponse

AGENT_CONTEXT_UNAVAILABLE_MESSAGE = "Agent context could not be loaded"
CONTEXT_PAGE = 1
CONTEXT_PAGE_SIZE = 20


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
        required_context=(AgentContextKind.PROJECTS, AgentContextKind.TASKS),
    )
    return {"analysis": analysis}


def load_context(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
    executor: ToolExecutor = execute_tool,
    gateway: AgentToolGateway | None = None,
) -> dict[str, object]:
    """Load a fixed first page through read tools and no lower-layer shortcut."""

    expected = (AgentContextKind.PROJECTS, AgentContextKind.TASKS)
    if state.analysis is None or state.analysis.required_context != expected:
        raise AgentContextUnavailableError(AGENT_CONTEXT_UNAVAILABLE_MESSAGE)

    read_only_context = AgentRuntimeContext(user_id=runtime_context.user_id)
    try:
        projects = executor(
            "list_projects",
            {
                "page": CONTEXT_PAGE,
                "page_size": CONTEXT_PAGE_SIZE,
                "include_archived": False,
            },
            read_only_context,
            gateway=gateway,
        )
        tasks = executor(
            "list_tasks",
            {"page": CONTEXT_PAGE, "page_size": CONTEXT_PAGE_SIZE},
            read_only_context,
            gateway=gateway,
        )
        if not isinstance(projects, ProjectListResponse) or not isinstance(
            tasks, TaskListResponse
        ):
            raise TypeError("unexpected tool result")
        snapshot = AgentContextSnapshot(projects=projects, tasks=tasks)
    except Exception:
        raise AgentContextUnavailableError(AGENT_CONTEXT_UNAVAILABLE_MESSAGE) from None
    return {"context": snapshot}
