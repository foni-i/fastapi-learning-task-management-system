"""Offline tests for provider proposals and deterministic batch validation."""

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from app.agent.context import AgentRuntimeContext
from app.agent.grounding import GroundedKnowledgeContext, GroundingEvidence
from app.agent.metrics import AgentRunMetrics
from app.agent.nodes.context import analyze_goal
from app.agent.nodes.planning import (
    PLAN_ACTION_INVALID,
    PLAN_CITATION_INVALID,
    PLAN_CONTEXT_MISSING,
    PLAN_PROPOSAL_INVALID,
    generate_plan,
    validate_plan,
)
from app.agent.planning import PLANNING_MAX_ATTEMPTS, PLANNING_TIMEOUT_SECONDS
from app.agent.prompt_budget import (
    MAX_PROMPT_INPUT_CHARACTERS,
    PROMPT_FINAL_SAFETY_MARGIN_CHARACTERS,
)
from app.agent.providers import (
    ProviderAuthenticationError,
    ProviderInvalidResponseError,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderTransientError,
    ProviderUsage,
)
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION, PlanningGoal
from app.agent.state import (
    AgentContextKind,
    AgentContextSnapshot,
    AgentGoalAnalysis,
    AgentGraphState,
    AgentPlanProposal,
    AgentPlanValidation,
    AgentValidationStatus,
    fingerprint_plan_proposal,
)
from app.core.exceptions import (
    AGENT_PLANNING_CONFIGURATION_MESSAGE,
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningConfigurationError,
    AgentPlanningUnavailableError,
)
from app.models.task import TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import PublicTask, TaskListResponse


def _proposal_payload(
    actions: list[dict[str, object]] | None = None,
    *,
    citation_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "planning_result": {
            "prompt_version": STUDY_PLAN_PROMPT_VERSION,
            "status": "completed",
            "plan": {
                "summary": "A bounded study plan",
                "steps": [
                    {
                        "step_key": "step_1",
                        "position": 1,
                        "title": "Read",
                        "description": "Read one chapter",
                        "success_criteria": "Notes exist",
                        "citation_ids": citation_ids or [],
                    }
                ],
            },
        },
        "actions": actions or [],
    }


class RecordingProvider:
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


def _ready_state() -> AgentGraphState:
    initial = AgentGraphState(goal=PlanningGoal(objective="Learn transactions"))
    analyzed = initial.model_copy(update=analyze_goal(initial))
    return analyzed.model_copy(
        update={
            "context": AgentContextSnapshot(
                projects=ProjectListResponse(
                    items=[], page=1, page_size=20, total=0, pages=0
                ),
                tasks=TaskListResponse(
                    items=[], page=1, page_size=20, total=0, pages=0
                ),
            )
        }
    )


def _response(actions: list[dict[str, object]] | None = None) -> ProviderResponse:
    return ProviderResponse(
        output_text=json.dumps(_proposal_payload(actions)),
        usage=ProviderUsage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def test_generate_plan_requests_exact_schema_and_returns_safe_metrics() -> None:
    provider = RecordingProvider([_response()])
    times = iter((10.0, 10.25))
    state = _ready_state()

    update = generate_plan(
        state,
        model="synthetic-model",
        provider=provider,
        clock=lambda: next(times),
        sleeper=lambda _: pytest.fail("success must not sleep"),
    )

    assert set(update) == {"proposal", "metrics"}
    proposal = update["proposal"]
    assert isinstance(proposal, AgentPlanProposal)
    assert proposal.actions == ()
    request, timeout = provider.requests[0]
    assert timeout == PLANNING_TIMEOUT_SECONDS
    assert request.prompt_version == STUDY_PLAN_PROMPT_VERSION
    assert request.output_schema_name == "AgentPlanProposal"
    assert request.output_schema == AgentPlanProposal.model_json_schema()
    assert request.tools == ()
    assert "Untrusted planning data JSON" in request.input
    assert state.goal.objective in request.input
    metrics = update["metrics"]
    assert isinstance(metrics, AgentRunMetrics)
    assert metrics.provider_attempt_count == 1
    assert metrics.total_tokens == 30
    assert metrics.latency_ms == 250.0


def test_generate_plan_budgets_maximum_public_tasks_before_provider() -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    goal = PlanningGoal(
        objective="目" * 2_000,
        constraints=tuple("限" * 500 for _ in range(20)),
    )
    analysis = AgentGoalAnalysis(
        objective=goal.objective,
        constraints=goal.constraints,
        required_context=(
            AgentContextKind.PROJECTS,
            AgentContextKind.TASKS,
            AgentContextKind.KNOWLEDGE,
        ),
    )
    tasks = [
        PublicTask(
            id=UUID(int=index + 1),
            project_id=UUID(int=100 + index),
            title="学" * 300,
            description="据" * 5_000,
            status=TaskStatus.IN_PROGRESS,
            priority=TaskPriority.HIGH,
            planned_date=date(2026, 1, 1),
            due_at=now,
            estimated_minutes=1_440,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        for index in range(20)
    ]
    state = AgentGraphState(
        goal=goal,
        analysis=analysis,
        context=AgentContextSnapshot(
            projects=ProjectListResponse(
                items=[], page=1, page_size=20, total=0, pages=0
            ),
            tasks=TaskListResponse(
                items=tasks,
                page=1,
                page_size=20,
                total=20,
                pages=1,
            ),
        ),
        approval_feedback="改" * 1_000,
    )
    provider = RecordingProvider([_response()])

    generated = generate_plan(
        state,
        model="synthetic-model",
        provider=provider,
    )

    request, _timeout = provider.requests[0]
    assert len(request.input) <= (
        MAX_PROMPT_INPUT_CHARACTERS - PROMPT_FINAL_SAFETY_MARGIN_CHARACTERS
    )
    assert '"truncation"' in request.input
    proposed = state.model_copy(update=generated)
    validation = validate_plan(
        proposed,
        runtime_context=AgentRuntimeContext(user_id=uuid4()),
    )["validation"]
    assert validation.status is AgentValidationStatus.VALID
    assert validation.proposal_fingerprint == fingerprint_plan_proposal(
        generated["proposal"]
    )


def test_generate_and_validate_one_to_three_write_actions() -> None:
    project_id = uuid4()
    task_id = uuid4()
    action_sets: list[list[dict[str, object]]] = [
        [
            {
                "action_key": "create_1",
                "tool_name": "create_task",
                "arguments": {"project_id": str(project_id), "title": "Read"},
            }
        ],
        [
            {
                "action_key": "create_1",
                "tool_name": "create_task",
                "arguments": {"project_id": str(project_id), "title": "Read"},
            },
            {
                "action_key": "update_1",
                "tool_name": "update_task",
                "arguments": {"task_id": str(task_id), "title": "Review"},
            },
        ],
    ]
    action_sets.append(
        [
            *action_sets[-1],
            {
                "action_key": "create_2",
                "tool_name": "create_task",
                "arguments": {"project_id": str(project_id), "title": "Practice"},
            },
        ]
    )

    for actions in action_sets:
        state = _ready_state()
        generated = generate_plan(
            state,
            model="synthetic-model",
            provider=RecordingProvider([_response(actions)]),
        )
        proposed = state.model_copy(update=generated)
        validated = validate_plan(
            proposed,
            runtime_context=AgentRuntimeContext(
                user_id=uuid4(), write_tools_enabled=True
            ),
        )
        assert validated["validation"].status is AgentValidationStatus.VALID


def test_invalid_action_rejects_whole_batch_without_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.tools as tools

    monkeypatch.setattr(
        tools,
        "AgentDomainGateway",
        lambda: pytest.fail("validation must not create a gateway or Session"),
    )
    state = _ready_state()
    generated = generate_plan(
        state,
        model="synthetic-model",
        provider=RecordingProvider(
            [
                _response(
                    [
                        {
                            "action_key": "create_1",
                            "tool_name": "create_task",
                            "arguments": {
                                "project_id": str(uuid4()),
                                "title": "Valid",
                            },
                        },
                        {
                            "action_key": "update_1",
                            "tool_name": "update_task",
                            "arguments": {"task_id": str(uuid4())},
                        },
                    ]
                )
            ]
        ),
    )
    update = validate_plan(
        state.model_copy(update=generated),
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
    )

    validation = update["validation"]
    assert isinstance(validation, AgentPlanValidation)
    assert validation.status is AgentValidationStatus.INVALID
    assert validation.error_code == PLAN_ACTION_INVALID


def test_write_capability_is_trusted_and_cannot_come_from_proposal() -> None:
    actions: list[dict[str, object]] = [
        {
            "action_key": "create_1",
            "tool_name": "create_task",
            "arguments": {"project_id": str(uuid4()), "title": "Read"},
        }
    ]
    state = _ready_state()
    generated = generate_plan(
        state,
        model="synthetic-model",
        provider=RecordingProvider([_response(actions)]),
    )
    proposed = state.model_copy(update=generated)

    update = validate_plan(
        proposed,
        runtime_context=AgentRuntimeContext(user_id=uuid4()),
    )
    validation = update["validation"]
    assert isinstance(validation, AgentPlanValidation)
    assert validation.status is AgentValidationStatus.INVALID
    assert validation.error_code == PLAN_ACTION_INVALID


def test_missing_context_and_tampered_proposal_fail_with_stable_codes() -> None:
    state = AgentGraphState(goal=PlanningGoal(objective="Learn"))
    missing = validate_plan(
        state,
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
    )
    assert missing["validation"].error_code == PLAN_CONTEXT_MISSING

    ready = _ready_state()
    valid = AgentPlanProposal.model_validate(_proposal_payload())
    tampered = valid.model_copy(
        update={
            "planning_result": valid.planning_result.model_copy(
                update={"prompt_version": "study-plan.v999"}
            )
        }
    )
    invalid_state = ready.model_copy(update={"proposal": tampered})
    invalid = validate_plan(
        invalid_state,
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
    )
    assert invalid["validation"].error_code == PLAN_PROPOSAL_INVALID


def test_citations_validate_before_approval_and_bind_proposal_fingerprint() -> None:
    document_id, chunk_id, other_chunk_id = uuid4(), uuid4(), uuid4()
    citation_id = f"knowledge:{document_id}:{chunk_id}"
    other_citation_id = f"knowledge:{document_id}:{other_chunk_id}"
    evidence = tuple(
        GroundingEvidence(
            citation_id=current_id,
            document_id=document_id,
            chunk_id=current_chunk_id,
            source="notes.txt",
            page_number=1,
            ordinal=index,
            excerpt="bounded evidence",
            lexical_rank=index + 1,
            fusion_score=1 / (61 + index),
        )
        for index, (current_id, current_chunk_id) in enumerate(
            ((citation_id, chunk_id), (other_citation_id, other_chunk_id))
        )
    )
    ready = _ready_state()
    assert ready.context is not None
    ready = ready.model_copy(
        update={
            "context": ready.context.model_copy(
                update={"knowledge": GroundedKnowledgeContext(evidence=evidence)}
            )
        }
    )

    valid_proposal = AgentPlanProposal.model_validate(
        _proposal_payload(citation_ids=[citation_id])
    )
    valid_state = ready.model_copy(update={"proposal": valid_proposal})
    valid = validate_plan(
        valid_state,
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
    )["validation"]
    assert valid.status is AgentValidationStatus.VALID
    assert valid.proposal_fingerprint == fingerprint_plan_proposal(valid_proposal)

    changed = AgentPlanProposal.model_validate(
        _proposal_payload(citation_ids=[other_citation_id])
    )
    assert fingerprint_plan_proposal(changed) != fingerprint_plan_proposal(
        valid_proposal
    )

    normalized_first = AgentPlanProposal.model_validate(
        _proposal_payload(citation_ids=[citation_id, other_citation_id])
    )
    normalized_second = AgentPlanProposal.model_validate(
        _proposal_payload(citation_ids=[other_citation_id, citation_id])
    )
    assert normalized_first == normalized_second
    assert fingerprint_plan_proposal(normalized_first) == fingerprint_plan_proposal(
        normalized_second
    )

    for references in ([], [f"knowledge:{uuid4()}:{uuid4()}"]):
        proposal = AgentPlanProposal.model_validate(
            _proposal_payload(citation_ids=references)
        )
        validation = validate_plan(
            ready.model_copy(update={"proposal": proposal}),
            runtime_context=AgentRuntimeContext(
                user_id=uuid4(), write_tools_enabled=True
            ),
        )["validation"]
        assert validation.status is AgentValidationStatus.INVALID
        assert validation.error_code == PLAN_CITATION_INVALID

    fabricated_without_evidence = AgentPlanProposal.model_validate(
        _proposal_payload(citation_ids=[citation_id])
    )
    validation = validate_plan(
        _ready_state().model_copy(update={"proposal": fabricated_without_evidence}),
        runtime_context=AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
    )["validation"]
    assert validation.error_code == PLAN_CITATION_INVALID


@pytest.mark.parametrize(
    "first_failure",
    [
        ProviderTimeoutError("private timeout"),
        ProviderTransientError("private transient"),
        ProviderInvalidResponseError("private response"),
    ],
    ids=["timeout", "transient", "invalid"],
)
def test_retryable_generation_failure_is_bounded(
    first_failure: Exception,
) -> None:
    provider = RecordingProvider([first_failure, _response()])
    sleeps: list[float] = []

    update = generate_plan(
        _ready_state(),
        model="synthetic-model",
        provider=provider,
        sleeper=sleeps.append,
    )

    assert isinstance(update["proposal"], AgentPlanProposal)
    assert len(provider.requests) == PLANNING_MAX_ATTEMPTS
    assert len(sleeps) == 1


def test_malformed_or_oversized_proposal_fails_safely_after_exact_attempts() -> None:
    unsafe = "private raw provider payload"
    four_actions: list[dict[str, object]] = [
        {
            "action_key": f"create_{index}",
            "tool_name": "create_task",
            "arguments": {"project_id": str(uuid4()), "title": "Read"},
        }
        for index in range(4)
    ]
    provider = RecordingProvider([_response(four_actions), _response(four_actions)])

    with pytest.raises(
        AgentPlanningUnavailableError,
        match=AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    ) as exc_info:
        generate_plan(
            _ready_state(),
            model="synthetic-model",
            provider=provider,
            sleeper=lambda _: None,
        )

    assert len(provider.requests) == PLANNING_MAX_ATTEMPTS
    assert unsafe not in str(exc_info.value)


def test_authentication_failure_is_not_retried_or_leaked() -> None:
    diagnostic = "private provider credential detail"
    provider = RecordingProvider([ProviderAuthenticationError(diagnostic)])

    with pytest.raises(
        AgentPlanningConfigurationError,
        match=AGENT_PLANNING_CONFIGURATION_MESSAGE,
    ) as exc_info:
        generate_plan(
            _ready_state(),
            model="synthetic-model",
            provider=provider,
        )

    assert len(provider.requests) == 1
    assert diagnostic not in str(exc_info.value)


def test_generated_update_round_trips_without_identity_or_provider_payloads() -> None:
    user_id = uuid4()
    state = _ready_state()
    generated = generate_plan(
        state,
        model="synthetic-model",
        provider=RecordingProvider([_response()]),
    )
    updated = state.model_copy(update=generated)
    round_tripped = AgentGraphState.model_validate_json(updated.model_dump_json())
    serialized = round_tripped.model_dump_json()

    assert round_tripped == updated
    assert str(user_id) not in serialized
    assert "api_key" not in serialized
    assert "raw_response" not in serialized
    assert "reasoning" not in serialized


def test_provider_cannot_return_read_unknown_identity_or_duplicate_actions() -> None:
    invalid_payloads = [
        _proposal_payload(
            [{"action_key": "read_1", "tool_name": "list_tasks", "arguments": {}}]
        ),
        _proposal_payload(
            [{"action_key": "other_1", "tool_name": "unknown", "arguments": {}}]
        ),
        _proposal_payload(
            [
                {
                    "action_key": "create_1",
                    "tool_name": "create_task",
                    "arguments": {"user_id": str(uuid4())},
                }
            ]
        ),
        _proposal_payload(
            [
                {
                    "action_key": "same",
                    "tool_name": "create_task",
                    "arguments": {"project_id": str(uuid4()), "title": "One"},
                },
                {
                    "action_key": "same",
                    "tool_name": "create_task",
                    "arguments": {"project_id": str(uuid4()), "title": "Two"},
                },
            ]
        ),
    ]

    for payload in invalid_payloads:
        provider = RecordingProvider(
            [
                ProviderResponse(output_text=json.dumps(payload)),
                ProviderResponse(output_text=json.dumps(payload)),
            ]
        )
        with pytest.raises(AgentPlanningUnavailableError):
            generate_plan(
                _ready_state(),
                model="synthetic-model",
                provider=provider,
                sleeper=lambda _: None,
            )
        assert len(provider.requests) == PLANNING_MAX_ATTEMPTS


def test_validation_is_deterministic() -> None:
    proposal = AgentPlanProposal.model_validate(_proposal_payload())
    state = _ready_state().model_copy(update={"proposal": proposal})
    context = AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True)

    first = validate_plan(state, runtime_context=context)
    second = validate_plan(state, runtime_context=context)

    assert first == second
    assert set(first) == {"validation"}
    assert first["validation"].status is AgentValidationStatus.VALID
