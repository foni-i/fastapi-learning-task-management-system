"""Scriptable provider fake that records only safe request metadata."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.agent.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderStreamChunk,
)


@dataclass(frozen=True)
class RecordedProviderCall:
    """Keep no prompt, model output, arguments, key, or identity."""

    prompt_version: str
    tool_names: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class ScriptedStream:
    """Configure safe text deltas followed by one normalized response."""

    response: ProviderResponse
    deltas: tuple[str, ...] = ()


ScriptedOutcome = ProviderResponse | ScriptedStream | Exception


class ScriptedAgentProvider:
    """Serve fixed generate/stream outcomes without network access."""

    def __init__(self, outcomes: Iterable[ScriptedOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[RecordedProviderCall] = []
        self.closed_streams = 0

    def _next(
        self, request: ProviderRequest, timeout_seconds: float
    ) -> ProviderResponse | ScriptedStream:
        self.calls.append(
            RecordedProviderCall(
                prompt_version=request.prompt_version,
                tool_names=tuple(tool.name for tool in request.tools),
                timeout_seconds=timeout_seconds,
            )
        )
        if not self._outcomes:
            raise AssertionError("scripted provider outcomes were exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        outcome = self._next(request, timeout_seconds)
        if isinstance(outcome, ScriptedStream):
            return outcome.response
        return outcome

    @contextmanager
    def stream(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> Iterator[Iterator[ProviderStreamChunk]]:
        try:
            outcome = self._next(request, timeout_seconds)
            if isinstance(outcome, ProviderResponse):
                scripted = ScriptedStream(response=outcome)
            else:
                scripted = outcome
            chunks = [
                ProviderStreamChunk(output_text_delta=delta)
                for delta in scripted.deltas
            ]
            chunks.append(ProviderStreamChunk(response=scripted.response))
            yield iter(chunks)
        finally:
            self.closed_streams += 1
