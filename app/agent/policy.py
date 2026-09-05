"""Code-owned capability risk policy for Agent Tool execution."""

HIGH_IMPACT_TOOL_NAMES = frozenset({"batch_create_tasks", "delete_task"})


def is_high_impact_tool(name: str) -> bool:
    """Return whether a capability requires persisted approval and idempotency."""

    return name in HIGH_IMPACT_TOOL_NAMES
