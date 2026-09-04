"""Pure routing decisions for the bounded Stage 9 workflow."""

from enum import StrEnum

from app.agent.state import AgentApprovalDecision, AgentGraphState

AGENT_APPROVAL_DECISION_MISSING_MESSAGE = "Agent approval decision is missing"


class AgentRoute(StrEnum):
    EXECUTE_TASKS = "execute_tasks"
    GENERATE_PLAN = "generate_plan"
    SUMMARIZE = "summarize"


class AgentRoutingError(RuntimeError):
    """Reject incomplete state without exposing plan or approval content."""


def route_after_approval(state: AgentGraphState) -> AgentRoute:
    """Choose the next node solely from the recorded approval decision."""

    if state.approval_decision is AgentApprovalDecision.APPROVED:
        return AgentRoute.EXECUTE_TASKS
    if state.approval_decision is AgentApprovalDecision.REQUEST_CHANGES:
        return AgentRoute.GENERATE_PLAN
    if state.approval_decision is AgentApprovalDecision.REJECTED:
        return AgentRoute.SUMMARIZE
    raise AgentRoutingError(AGENT_APPROVAL_DECISION_MISSING_MESSAGE)
