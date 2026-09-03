"""Trusted runtime values kept outside model-visible Agent arguments."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    """Carry authenticated identity and host-granted write capability."""

    user_id: UUID
    write_tools_enabled: bool = False
