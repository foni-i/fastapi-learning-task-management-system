"""Safe immutable metrics for one bounded Agent run."""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent.providers import ProviderUsage


class AgentRunOutcome(StrEnum):
    """Allowlisted terminal outcomes without diagnostic detail."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentRunMetrics(BaseModel):
    """Expose only numeric execution evidence and a prompt version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_version: str = Field(pattern=r"^study-plan\.v[1-9][0-9]*$")
    outcome: AgentRunOutcome
    model_round_count: int = Field(ge=0)
    provider_attempt_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)


def _sum_known(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def aggregate_usage(usages: Sequence[ProviderUsage]) -> ProviderUsage:
    """Sum supplied counters while preserving wholly missing fields as None."""

    return ProviderUsage(
        input_tokens=_sum_known([usage.input_tokens for usage in usages]),
        output_tokens=_sum_known([usage.output_tokens for usage in usages]),
        total_tokens=_sum_known([usage.total_tokens for usage in usages]),
    )


def build_run_metrics(
    *,
    prompt_version: str,
    outcome: AgentRunOutcome,
    model_round_count: int,
    provider_attempt_count: int,
    tool_call_count: int,
    usages: Sequence[ProviderUsage],
    started_at: float,
    finished_at: float,
) -> AgentRunMetrics:
    """Build one serializable aggregate from an injected monotonic clock."""

    if finished_at < started_at:
        raise ValueError("Agent metrics clock must be monotonic")
    usage = aggregate_usage(usages)
    return AgentRunMetrics(
        prompt_version=prompt_version,
        outcome=outcome,
        model_round_count=model_round_count,
        provider_attempt_count=provider_attempt_count,
        tool_call_count=tool_call_count,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        latency_ms=(finished_at - started_at) * 1000,
    )
