"""R6 deterministic evaluation harness over production Agent boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid5

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.agent.context import AgentRuntimeContext
from app.agent.evaluation import (
    EvaluationAggregateV2,
    EvaluationApprovalStimulus,
    EvaluationCaseEvidenceV2,
    EvaluationCaseV2,
    EvaluationCitationStimulus,
    EvaluationErrorCode,
    EvaluationInputPath,
    EvaluationObservationV2,
    EvaluationOutcome,
    EvaluationReportV2,
    EvaluationScenarioV2,
    LoadedEvaluationDatasetV2,
    MalformedEvaluationCaseV2,
)
from app.agent.graph import build_agent_graph
from app.agent.grounding import (
    GROUNDING_END,
    GroundedKnowledgeContext,
    GroundingValidationError,
    build_grounded_knowledge,
    render_untrusted_grounding,
    validate_citation_references,
)
from app.agent.nodes.approval import AgentApprovalRequest, AgentApprovalResponse
from app.agent.nodes.context import analyze_goal
from app.agent.providers import ProviderRequest, ProviderResponse
from app.agent.schemas import PlanningGoal
from app.agent.state import (
    AgentApprovalDecision,
    AgentGoalAnalysis,
    AgentGraphInput,
    AgentGraphState,
    AgentTerminalStatus,
)
from app.agent.tools import (
    AgentToolGateway,
    AgentToolInputError,
    AgentToolNotAllowedError,
    validate_tool_arguments,
)
from app.schemas.knowledge_retrieval import KnowledgeSearchResult

EVALUATION_USER_ID = UUID("00000000-0000-4000-8000-000000000101")
EVALUATION_PROJECT_ID = UUID("00000000-0000-4000-8000-000000000102")
EVALUATION_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000103")
EVALUATION_CHUNK_ID = UUID("00000000-0000-4000-8000-000000000104")
EVALUATION_UNKNOWN_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000105")
EVALUATION_UNKNOWN_CHUNK_ID = UUID("00000000-0000-4000-8000-000000000106")
EVALUATION_CITATION_ID = f"knowledge:{EVALUATION_DOCUMENT_ID}:{EVALUATION_CHUNK_ID}"
EVALUATION_UNKNOWN_CITATION_ID = (
    f"knowledge:{EVALUATION_UNKNOWN_DOCUMENT_ID}:{EVALUATION_UNKNOWN_CHUNK_ID}"
)
_THREAD_NAMESPACE = UUID("00000000-0000-4000-8000-000000000107")
_CHECKPOINT_TYPE_ALLOWLIST = (
    ("app.agent.metrics", "AgentRunMetrics"),
    ("app.agent.metrics", "AgentRunOutcome"),
    ("app.agent.schemas", "PlanningStatus"),
    ("app.agent.state", "AgentActionExecutionRecord"),
    ("app.agent.state", "AgentApprovalDecision"),
    ("app.agent.state", "AgentContextKind"),
    ("app.agent.state", "AgentContextSnapshot"),
    ("app.agent.state", "AgentExecutionOutcome"),
    ("app.agent.state", "AgentGoalAnalysis"),
    ("app.agent.state", "AgentPlanProposal"),
    ("app.agent.state", "AgentPlanValidation"),
    ("app.agent.state", "AgentTerminalStatus"),
    ("app.agent.state", "AgentValidationStatus"),
    ("app.agent.state", "AgentVerification"),
    ("app.agent.state", "AgentVerificationStatus"),
    ("app.agent.state", "AgentWriteToolName"),
    ("app.models.task", "TaskPriority"),
    ("app.models.task", "TaskStatus"),
)


class EvaluationProviderPort(Protocol):
    requests: list[ProviderRequest]
    responses: list[ProviderResponse]
    failure_count: int

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse: ...


@dataclass(frozen=True, slots=True)
class EvaluationGatewayStimulus:
    """Give a fake gateway dependency input without case gold or identity."""

    input_text: str
    input_path: EvaluationInputPath
    embedding_succeeds: bool
    retrieval_succeeds: bool


class EvaluationGatewayPort(Protocol):
    write_count: int
    search_failure_count: int
    last_search_result: KnowledgeSearchResult | None

    @property
    def embedding_call_count(self) -> int: ...

    @property
    def repository_call_count(self) -> int: ...


EvaluationProviderFactory = Callable[[EvaluationScenarioV2], EvaluationProviderPort]
EvaluationGatewayFactory = Callable[[EvaluationGatewayStimulus], EvaluationGatewayPort]


@dataclass(frozen=True, slots=True)
class EvaluationDependenciesV2:
    """Inject only dependency factories; expectations have no data edge here."""

    provider_factory: EvaluationProviderFactory
    gateway_factory: EvaluationGatewayFactory


class _SequenceClock:
    def __init__(self, values: tuple[float, float]) -> None:
        self._values = values
        self.calls: list[float] = []

    def __call__(self) -> float:
        index = min(len(self.calls), len(self._values) - 1)
        value = self._values[index]
        self.calls.append(value)
        return value

    @property
    def elapsed_ms(self) -> float | None:
        if len(self.calls) < 2:
            return None
        return round(max(0.0, (self.calls[-1] - self.calls[0]) * 1000), 6)


class _ApprovalDecider:
    def __init__(self, decision: EvaluationApprovalStimulus) -> None:
        self._decision = decision
        self.requests: list[AgentApprovalRequest] = []

    def decide(self, request: AgentApprovalRequest) -> AgentApprovalResponse:
        self.requests.append(request)
        if self._decision is EvaluationApprovalStimulus.REJECT:
            return AgentApprovalResponse(decision=AgentApprovalDecision.REJECTED)
        return AgentApprovalResponse(decision=AgentApprovalDecision.APPROVED)


def _evaluation_checkpointer() -> InMemorySaver:
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=_CHECKPOINT_TYPE_ALLOWLIST,
    )
    return InMemorySaver(serde=serializer)


def _tool_observation(
    case: EvaluationCaseV2,
) -> tuple[bool | None, bool | None]:
    scenario = case.scenario
    if scenario.tool_name is None or scenario.tool_arguments is None:
        return None, None
    try:
        validate_tool_arguments(
            scenario.tool_name,
            scenario.tool_arguments,
            AgentRuntimeContext(user_id=EVALUATION_USER_ID, write_tools_enabled=True),
        )
    except AgentToolNotAllowedError:
        return False, False
    except AgentToolInputError:
        return True, False
    return True, True


def _citation_references(
    stimulus: EvaluationCitationStimulus,
) -> tuple[str, ...]:
    if stimulus is EvaluationCitationStimulus.FIXTURE:
        return (EVALUATION_CITATION_ID,)
    if stimulus is EvaluationCitationStimulus.UNKNOWN:
        return (EVALUATION_UNKNOWN_CITATION_ID,)
    if stimulus is EvaluationCitationStimulus.DUPLICATE:
        return (EVALUATION_CITATION_ID, EVALUATION_CITATION_ID)
    return ()


def _citation_observation(
    case: EvaluationCaseV2,
    result: KnowledgeSearchResult | None,
) -> bool | None:
    stimulus = case.scenario.citation
    if stimulus is EvaluationCitationStimulus.NOT_APPLICABLE:
        return None
    context = (
        GroundedKnowledgeContext()
        if result is None
        else build_grounded_knowledge(result)
    )
    try:
        validate_citation_references((_citation_references(stimulus),), context)
    except GroundingValidationError:
        return False
    return True


def _matches_expectation(
    observation: EvaluationObservationV2,
    case: EvaluationCaseV2,
) -> bool:
    expected = case.expectation.model_dump(exclude_none=True)
    actual = observation.model_dump()
    return all(actual[name] == value for name, value in expected.items())


def _provider_usage(
    provider: EvaluationProviderPort,
) -> tuple[int | None, int | None, int | None]:
    if not provider.responses:
        return None, None, None
    input_values = tuple(response.usage.input_tokens for response in provider.responses)
    output_values = tuple(
        response.usage.output_tokens for response in provider.responses
    )
    input_tokens = (
        sum(value for value in input_values if value is not None)
        if all(value is not None for value in input_values)
        else None
    )
    output_tokens = (
        sum(value for value in output_values if value is not None)
        if all(value is not None for value in output_values)
        else None
    )
    total_tokens = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return input_tokens, output_tokens, total_tokens


def _error_code(
    *,
    output_status: AgentTerminalStatus | None,
    provider: EvaluationProviderPort,
    gateway: EvaluationGatewayPort,
) -> EvaluationErrorCode:
    if output_status is not AgentTerminalStatus.FAILED:
        return EvaluationErrorCode.NONE
    if provider.failure_count:
        return EvaluationErrorCode.MODEL_FAILED
    if gateway.search_failure_count and gateway.repository_call_count == 0:
        return EvaluationErrorCode.EMBEDDING_FAILED
    if gateway.search_failure_count:
        return EvaluationErrorCode.RETRIEVAL_FAILED
    return EvaluationErrorCode.VALIDATION_FAILED


def _run_case_v2(
    case: EvaluationCaseV2,
    dependencies: EvaluationDependenciesV2,
) -> EvaluationCaseEvidenceV2:
    goal = PlanningGoal(objective=case.input.text)
    analysis = cast(
        AgentGoalAnalysis,
        analyze_goal(AgentGraphState(goal=goal))["analysis"],
    )
    goal_propagated = analysis.objective == goal.objective
    provider = dependencies.provider_factory(case.scenario)
    gateway = dependencies.gateway_factory(
        EvaluationGatewayStimulus(
            input_text=goal.objective,
            input_path=case.scenario.input_path,
            embedding_succeeds=case.scenario.embedding.value == "succeeds",
            retrieval_succeeds=case.scenario.retrieval.value == "succeeds",
        )
    )
    clock = _SequenceClock(case.scenario.clock_seconds)
    decider = _ApprovalDecider(case.scenario.approval)
    durable = case.scenario.approval is not EvaluationApprovalStimulus.NOT_APPLICABLE
    workflow = build_agent_graph(
        model="synthetic-evaluation-model",
        provider=provider,
        gateway=cast(AgentToolGateway, gateway),
        runtime_context=AgentRuntimeContext(
            user_id=EVALUATION_USER_ID,
            write_tools_enabled=True,
        ),
        approval_decider=decider,
        clock=clock,
        sleeper=lambda _seconds: None,
        checkpointer=_evaluation_checkpointer() if durable else None,
        durable_approval=durable,
    )

    output_status: AgentTerminalStatus | None = None
    approval_respected: bool | None = None
    if durable:
        thread_id = uuid5(_THREAD_NAMESPACE, case.case_id)
        progress = workflow.start_durable(
            AgentGraphInput(goal=goal),
            thread_id=thread_id,
        )
        approval_respected = progress.approval is not None and gateway.write_count == 0
        if case.scenario.approval in {
            EvaluationApprovalStimulus.APPROVE,
            EvaluationApprovalStimulus.REJECT,
        }:
            decision = (
                AgentApprovalDecision.APPROVED
                if case.scenario.approval is EvaluationApprovalStimulus.APPROVE
                else AgentApprovalDecision.REJECTED
            )
            progress = workflow.resume_durable(
                AgentApprovalResponse(decision=decision),
                thread_id=thread_id,
            )
            output_status = None if progress.output is None else progress.output.status
    else:
        output_status = workflow.invoke(AgentGraphInput(goal=goal)).status

    tool_name_valid, tool_arguments_valid = _tool_observation(case)
    citation_valid = _citation_observation(case, gateway.last_search_result)
    grounding_propagated: bool | None = None
    grounding_delimiter_escaped: bool | None = None
    if case.scenario.input_path is EvaluationInputPath.GROUNDING:
        result = gateway.last_search_result
        grounding_propagated = bool(
            result and result.items and result.items[0].excerpt == goal.objective
        )
        if result is not None:
            rendered = render_untrusted_grounding(build_grounded_knowledge(result))
            grounding_delimiter_escaped = (
                GROUNDING_END in goal.objective and rendered.count(GROUNDING_END) == 1
            )

    requests = provider.requests
    if requests:
        goal_propagated = goal_propagated and all(
            goal.objective in request.input for request in requests
        )
    provider_called = bool(requests)
    prompt_version = requests[-1].prompt_version if requests else None
    input_tokens, output_tokens, total_tokens = _provider_usage(provider)
    plan_valid = (
        None
        if not provider_called
        else output_status is not AgentTerminalStatus.FAILED
        or approval_respected is True
    )
    observation = EvaluationObservationV2(
        error_code=_error_code(
            output_status=output_status,
            provider=provider,
            gateway=gateway,
        ),
        goal_propagated=goal_propagated,
        goal_character_count=len(goal.objective),
        grounding_propagated=grounding_propagated,
        grounding_delimiter_escaped=grounding_delimiter_escaped,
        provider_called=provider_called,
        prompt_version=prompt_version,
        plan_valid=plan_valid,
        tool_name_valid=tool_name_valid,
        tool_arguments_valid=tool_arguments_valid,
        citation_valid=citation_valid,
        approval_respected=approval_respected,
        write_count=gateway.write_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=clock.elapsed_ms,
    )
    outcome = (
        EvaluationOutcome.SUCCEEDED
        if _matches_expectation(observation, case)
        else EvaluationOutcome.FAILED
    )
    return EvaluationCaseEvidenceV2(
        case_id=case.case_id,
        category=case.category,
        outcome=outcome,
        observation=observation,
        error_code=observation.error_code,
    )


def run_evaluation_v2(
    dataset: LoadedEvaluationDatasetV2,
    dependencies: EvaluationDependenciesV2,
) -> EvaluationReportV2:
    """Run v2 cases without passing expectations into any dependency."""

    evidence: list[EvaluationCaseEvidenceV2] = []
    for entry in dataset.entries:
        if isinstance(entry, MalformedEvaluationCaseV2):
            evidence.append(
                EvaluationCaseEvidenceV2(
                    case_id=entry.case_id,
                    category=None,
                    outcome=EvaluationOutcome.FAILED,
                    observation=None,
                    error_code=EvaluationErrorCode.CASE_INVALID,
                )
            )
            continue
        evidence.append(_run_case_v2(entry, dependencies))

    succeeded = sum(item.outcome is EvaluationOutcome.SUCCEEDED for item in evidence)
    malformed = sum(item.observation is None for item in evidence)
    unintended_writes = sum(
        item.observation.write_count
        for item, entry in zip(evidence, dataset.entries, strict=True)
        if item.observation is not None
        and isinstance(entry, EvaluationCaseV2)
        and entry.scenario.approval is not EvaluationApprovalStimulus.APPROVE
    )
    return EvaluationReportV2(
        cases=tuple(evidence),
        aggregate=EvaluationAggregateV2(
            total_cases=len(evidence),
            succeeded=succeeded,
            failed=len(evidence) - succeeded,
            malformed=malformed,
            unintended_writes=unintended_writes,
        ),
    )
