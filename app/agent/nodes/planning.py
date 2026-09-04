"""Provider-backed proposal generation and deterministic plan validation."""

from time import monotonic, sleep
from typing import Never, TypedDict

from pydantic import ValidationError

from app.agent.context import AgentRuntimeContext
from app.agent.metrics import AgentRunMetrics, AgentRunOutcome, build_run_metrics
from app.agent.planning import (
    PLANNING_MAX_ATTEMPTS,
    PLANNING_RETRY_DELAY_SECONDS,
    PLANNING_TIMEOUT_SECONDS,
    Clock,
    Sleeper,
)
from app.agent.prompts import build_agent_plan_proposal_prompt
from app.agent.providers import (
    ModelProvider,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderPermissionError,
    ProviderRequest,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUsage,
)
from app.agent.state import (
    AgentGraphState,
    AgentPlanProposal,
    AgentPlanValidation,
    AgentValidationStatus,
    fingerprint_plan_proposal,
)
from app.agent.tools import validate_tool_arguments
from app.core.exceptions import (
    AGENT_PLANNING_CONFIGURATION_MESSAGE,
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningConfigurationError,
    AgentPlanningUnavailableError,
)

PLAN_CONTEXT_MISSING = "PLAN_CONTEXT_MISSING"
PLAN_PROPOSAL_MISSING = "PLAN_PROPOSAL_MISSING"
PLAN_PROPOSAL_INVALID = "PLAN_PROPOSAL_INVALID"
PLAN_ACTION_INVALID = "PLAN_ACTION_INVALID"


class AgentPlanGenerationUpdate(TypedDict):
    proposal: AgentPlanProposal
    metrics: AgentRunMetrics


class AgentPlanValidationUpdate(TypedDict):
    validation: AgentPlanValidation


def _fail_unavailable() -> Never:
    raise AgentPlanningUnavailableError(AGENT_PLANNING_UNAVAILABLE_MESSAGE) from None


def generate_plan(
    state: AgentGraphState,
    *,
    model: str,
    provider: ModelProvider,
    clock: Clock = monotonic,
    sleeper: Sleeper = sleep,
) -> AgentPlanGenerationUpdate:
    """Generate one strict proposal without granting approval or executing it."""

    analysis = state.analysis
    context = state.context
    if analysis is None or context is None:
        _fail_unavailable()
    started_at = clock()
    usages: list[ProviderUsage] = []
    try:
        prompt = build_agent_plan_proposal_prompt(
            state.goal,
            analysis,
            context,
        )
        request = ProviderRequest(
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.instructions,
            input=prompt.input,
            output_schema_name="AgentPlanProposal",
            output_schema=AgentPlanProposal.model_json_schema(),
        )
    except TypeError, ValueError, ValidationError:
        _fail_unavailable()

    for attempt in range(1, PLANNING_MAX_ATTEMPTS + 1):
        try:
            response = provider.generate(
                request,
                timeout_seconds=PLANNING_TIMEOUT_SECONDS,
            )
            usages.append(response.usage)
            if response.output_text is None or response.tool_calls:
                raise ProviderInvalidResponseError(
                    "Model provider returned an invalid response"
                )
            proposal = AgentPlanProposal.model_validate_json(response.output_text)
            metrics = build_run_metrics(
                prompt_version=prompt.version,
                outcome=AgentRunOutcome.SUCCEEDED,
                model_round_count=1,
                provider_attempt_count=attempt,
                tool_call_count=0,
                usages=usages,
                started_at=started_at,
                finished_at=clock(),
            )
            return {"proposal": proposal, "metrics": metrics}
        except (
            ProviderTimeoutError,
            ProviderTransientError,
            ProviderInvalidResponseError,
            ValidationError,
        ):
            if attempt == PLANNING_MAX_ATTEMPTS:
                _fail_unavailable()
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
            _fail_unavailable()
    raise AssertionError("bounded plan proposal generation did not terminate")


def _invalid(code: str, *, revision: int) -> AgentPlanValidationUpdate:
    return {
        "validation": AgentPlanValidation(
            status=AgentValidationStatus.INVALID,
            revision=revision,
            error_code=code,
        )
    }


def validate_plan(
    state: AgentGraphState,
    *,
    runtime_context: AgentRuntimeContext,
) -> AgentPlanValidationUpdate:
    """Validate the full proposed write batch without opening a gateway."""

    if state.analysis is None or state.context is None:
        return _invalid(PLAN_CONTEXT_MISSING, revision=state.revision_count)
    if state.proposal is None:
        return _invalid(PLAN_PROPOSAL_MISSING, revision=state.revision_count)
    try:
        proposal = AgentPlanProposal.model_validate(
            state.proposal.model_dump(mode="python")
        )
    except TypeError, ValueError, ValidationError:
        return _invalid(PLAN_PROPOSAL_INVALID, revision=state.revision_count)

    try:
        for action in proposal.actions:
            validate_tool_arguments(
                action.tool_name.value,
                action.arguments,
                runtime_context,
            )
    except Exception:
        return _invalid(PLAN_ACTION_INVALID, revision=state.revision_count)
    return {
        "validation": AgentPlanValidation(
            status=AgentValidationStatus.VALID,
            revision=state.revision_count,
            proposal_fingerprint=fingerprint_plan_proposal(proposal),
        ),
    }
