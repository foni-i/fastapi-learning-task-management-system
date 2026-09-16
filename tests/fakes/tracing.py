"""Deterministic in-memory sinks for ordinary Agent tracing tests."""

from app.agent.tracing import AgentTraceEvent


class RecordingTraceSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[AgentTraceEvent] = []
        self.calls = 0
        self.fail = fail

    def emit(self, event: AgentTraceEvent) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("synthetic private sink diagnostic")
        self.events.append(event)
