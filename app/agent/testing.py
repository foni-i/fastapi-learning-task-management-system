"""Internal fake-driven entry point; never accepts a caller-selected owner."""

from collections.abc import Mapping
from time import monotonic

from app.agent.context import AgentRuntimeContext
from app.agent.loop import AgentLoopExecution, MonotonicClock, _run_agent
from app.agent.providers import ModelProvider
from app.agent.schemas import PlanningGoal
from app.agent.tools import AgentToolGateway


def run_agent_for_test(
    goal: PlanningGoal | Mapping[str, object],
    *,
    context: AgentRuntimeContext,
    model: str,
    provider: ModelProvider,
    gateway: AgentToolGateway,
    clock: MonotonicClock = monotonic,
) -> AgentLoopExecution:
    """Expose safe loop metadata to deterministic internal tests."""

    return _run_agent(
        goal,
        context=context,
        model=model,
        provider=provider,
        gateway=gateway,
        clock=clock,
    )
