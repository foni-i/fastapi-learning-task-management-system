"""Bounded structured planning orchestration over the provider Protocol."""

from collections.abc import Callable, Mapping
from time import monotonic, sleep

from pydantic import ValidationError

from app.agent.prompts import build_study_plan_prompt
from app.agent.providers import (
    ModelProvider,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderPermissionError,
    ProviderRequest,
    ProviderTimeoutError,
    ProviderTransientError,
)
from app.agent.schemas import PlanningGoal, PlanningResult
from app.core.exceptions import (
    AGENT_PLANNING_CONFIGURATION_MESSAGE,
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningConfigurationError,
    AgentPlanningUnavailableError,
)

PLANNING_TIMEOUT_SECONDS = 30.0
PLANNING_MAX_ATTEMPTS = 2
PLANNING_RETRY_DELAY_SECONDS = 0.1

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


def generate_study_plan(
    goal: PlanningGoal | Mapping[str, object],
    *,
    model: str,
    provider: ModelProvider,
    clock: Clock = monotonic,
    sleeper: Sleeper = sleep,
) -> PlanningResult:
    """Return one strict plan or a fixed safe failure within exact attempt bounds."""

    validated_goal = PlanningGoal.model_validate(goal)
    prompt = build_study_plan_prompt(validated_goal)
    request = ProviderRequest(
        model=model,
        prompt_version=prompt.version,
        instructions=prompt.instructions,
        input=prompt.input,
        output_schema_name="PlanningResult",
        output_schema=PlanningResult.model_json_schema(),
    )

    for attempt in range(1, PLANNING_MAX_ATTEMPTS + 1):
        clock()
        try:
            response = provider.generate(
                request,
                timeout_seconds=PLANNING_TIMEOUT_SECONDS,
            )
            if response.output_text is None:
                raise ProviderInvalidResponseError(
                    "Model provider returned an invalid response"
                )
            try:
                return PlanningResult.model_validate_json(response.output_text)
            except ValidationError:
                raise ProviderInvalidResponseError(
                    "Model provider returned an invalid response"
                ) from None
        except (
            ProviderTimeoutError,
            ProviderTransientError,
            ProviderInvalidResponseError,
        ):
            if attempt == PLANNING_MAX_ATTEMPTS:
                raise AgentPlanningUnavailableError(
                    AGENT_PLANNING_UNAVAILABLE_MESSAGE
                ) from None
            sleeper(PLANNING_RETRY_DELAY_SECONDS)
        except (
            ProviderConfigurationError,
            ProviderAuthenticationError,
            ProviderPermissionError,
        ):
            raise AgentPlanningConfigurationError(
                AGENT_PLANNING_CONFIGURATION_MESSAGE
            ) from None
        except Exception:
            raise AgentPlanningUnavailableError(
                AGENT_PLANNING_UNAVAILABLE_MESSAGE
            ) from None
        finally:
            clock()

    raise AssertionError("bounded planning loop did not terminate")
