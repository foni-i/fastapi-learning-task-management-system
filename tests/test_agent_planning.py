"""Bounded offline orchestration tests for structured study planning."""

from collections.abc import Iterable

import pytest
from pydantic import ValidationError

from app.agent.planning import (
    PLANNING_MAX_ATTEMPTS,
    PLANNING_RETRY_DELAY_SECONDS,
    PLANNING_TIMEOUT_SECONDS,
    generate_study_plan,
)
from app.agent.providers import (
    ProviderAuthenticationError,
    ProviderInvalidResponseError,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderTransientError,
)
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION, PlanningGoal
from app.core.exceptions import (
    AGENT_PLANNING_CONFIGURATION_MESSAGE,
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningConfigurationError,
    AgentPlanningUnavailableError,
)

VALID_RESULT_JSON = """{
  "prompt_version": "study-plan.v2",
  "status": "completed",
  "plan": {
    "summary": "A safe plan",
    "steps": [{
      "step_key": "step_1",
      "position": 1,
      "title": "Read",
      "description": "Read the chapter",
      "success_criteria": "Notes are complete"
    }]
  }
}"""


class ScriptedProvider:
    def __init__(self, outcomes: Iterable[ProviderResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple[ProviderRequest, float]] = []

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.requests.append((request, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _success() -> ProviderResponse:
    return ProviderResponse(output_text=VALID_RESULT_JSON)


def test_first_attempt_returns_strict_versioned_plan() -> None:
    provider = ScriptedProvider([_success()])
    clock_calls: list[float] = []

    def clock() -> float:
        clock_calls.append(1.0)
        return 1.0

    result = generate_study_plan(
        PlanningGoal(objective="Learn transactions"),
        model="synthetic-model",
        provider=provider,
        clock=clock,
        sleeper=lambda _: pytest.fail("success must not sleep"),
    )

    assert result.prompt_version == STUDY_PLAN_PROMPT_VERSION
    assert result.plan.steps[0].step_key == "step_1"
    assert len(provider.requests) == 1
    request, timeout = provider.requests[0]
    assert timeout == PLANNING_TIMEOUT_SECONDS
    assert request.prompt_version == STUDY_PLAN_PROMPT_VERSION
    assert request.output_schema_name == "PlanningResult"
    assert request.output_schema == result.__class__.model_json_schema()
    assert len(clock_calls) == 2


@pytest.mark.parametrize(
    "first_failure",
    [
        ProviderTimeoutError("unsafe timeout detail"),
        ProviderTransientError("unsafe transient detail"),
        ProviderInvalidResponseError("unsafe response detail"),
    ],
    ids=["timeout", "transient", "invalid-structured-output"],
)
def test_one_retry_can_recover_from_classified_failure(
    first_failure: Exception,
) -> None:
    provider = ScriptedProvider([first_failure, _success()])
    sleeps: list[float] = []

    result = generate_study_plan(
        {"objective": "Learn transactions"},
        model="synthetic-model",
        provider=provider,
        sleeper=sleeps.append,
    )

    assert result.status == "completed"
    assert len(provider.requests) == PLANNING_MAX_ATTEMPTS
    assert sleeps == [PLANNING_RETRY_DELAY_SECONDS]


def test_malformed_result_is_retried_once_then_fails_safely() -> None:
    raw_output = "not a valid planning result"
    provider = ScriptedProvider(
        [
            ProviderResponse(output_text=raw_output),
            ProviderResponse(output_text=raw_output),
        ]
    )

    with pytest.raises(
        AgentPlanningUnavailableError,
        match=AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    ) as exc_info:
        generate_study_plan(
            PlanningGoal(objective="Learn transactions"),
            model="synthetic-model",
            provider=provider,
            sleeper=lambda _: None,
        )

    assert len(provider.requests) == PLANNING_MAX_ATTEMPTS
    assert raw_output not in str(exc_info.value)


def test_non_retryable_provider_failure_is_mapped_once() -> None:
    diagnostic = "synthetic secret provider diagnostic"
    provider = ScriptedProvider([ProviderAuthenticationError(diagnostic)])

    with pytest.raises(
        AgentPlanningConfigurationError,
        match=AGENT_PLANNING_CONFIGURATION_MESSAGE,
    ) as exc_info:
        generate_study_plan(
            PlanningGoal(objective="Learn transactions"),
            model="synthetic-model",
            provider=provider,
        )

    assert len(provider.requests) == 1
    assert diagnostic not in str(exc_info.value)


def test_exhausted_transient_failures_do_not_exceed_two_attempts() -> None:
    provider = ScriptedProvider(
        [
            ProviderTransientError("first unsafe detail"),
            ProviderTransientError("second unsafe detail"),
        ]
    )

    with pytest.raises(
        AgentPlanningUnavailableError,
        match=AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    ):
        generate_study_plan(
            PlanningGoal(objective="Learn transactions"),
            model="synthetic-model",
            provider=provider,
            sleeper=lambda _: None,
        )

    assert len(provider.requests) == 2


def test_invalid_goal_fails_before_provider_use() -> None:
    provider = ScriptedProvider([_success()])

    with pytest.raises(ValidationError):
        generate_study_plan(
            {"objective": " "},
            model="synthetic-model",
            provider=provider,
        )

    assert provider.requests == []
