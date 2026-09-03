"""Deterministic offline tests for the bounded internal Agent loop."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.agent.context import AgentRuntimeContext
from app.agent.loop import (
    MAX_MODEL_ROUNDS,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TOTAL_TOOL_CALLS,
    AgentLoopExecution,
    run_agent,
)
from app.agent.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderTransientError,
)
from app.agent.schemas import PlanningResult
from app.agent.testing import run_agent_for_test
from app.core.exceptions import (
    AGENT_PLANNING_UNAVAILABLE_MESSAGE,
    AgentPlanningUnavailableError,
)
from app.models import TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)

FINAL_RESULT = """{"prompt_version":"study-plan.v1","status":"completed","plan":{"summary":"A bounded plan","steps":[{"step_key":"step_1","position":1,"title":"Study","description":"Complete the selected task","success_criteria":"The task is reviewed"}]}}"""


class ScriptedProvider:
    def __init__(self, responses: Iterable[ProviderResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[ProviderRequest] = []

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeGateway:
    def __init__(self) -> None:
        self.users: list[UUID] = []

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        self.users.append(user_id)
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        self.users.append(user_id)
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        self.users.append(user_id)
        return _task(task_input.project_id)

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        self.users.append(user_id)
        return _task(uuid4(), task_id=task_id)


def _task(project_id: UUID, *, task_id: UUID | None = None) -> PublicTask:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    return PublicTask(
        id=task_id or uuid4(),
        project_id=project_id,
        title="Study",
        description=None,
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


def _call(
    call_id: str, name: str = "list_tasks", **arguments: object
) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=arguments)


def _run(
    responses: Iterable[ProviderResponse | Exception], *, writable: bool = False
) -> tuple[AgentLoopExecution, ScriptedProvider, FakeGateway, UUID]:
    provider = ScriptedProvider(responses)
    gateway = FakeGateway()
    user_id = uuid4()
    result = run_agent_for_test(
        {"objective": "Plan a study session"},
        context=AgentRuntimeContext(user_id=user_id, write_tools_enabled=writable),
        model="synthetic-model",
        provider=provider,
        gateway=gateway,
    )
    return result, provider, gateway, user_id


def test_no_tool_response_returns_strict_plan_in_one_round() -> None:
    execution, provider, gateway, _ = _run([ProviderResponse(output_text=FINAL_RESULT)])
    assert isinstance(execution.result, PlanningResult)
    assert execution.trace.round_count == 1
    assert execution.trace.tool_count == 0
    assert gateway.users == []
    assert len(provider.requests) == 1


def test_read_tool_then_plan_uses_safe_feedback_and_trusted_identity() -> None:
    execution, provider, gateway, user_id = _run(
        [
            ProviderResponse(tool_calls=(_call("call_1"),)),
            ProviderResponse(output_text=FINAL_RESULT),
        ]
    )
    assert execution.trace.round_count == 2
    assert execution.trace.tool_records[0].tool_name == "list_tasks"
    assert gateway.users == [user_id]
    assert "Validated tool result data" in provider.requests[1].input
    assert str(user_id) not in provider.requests[1].input


def test_write_tool_requires_and_uses_trusted_capability() -> None:
    project_id = uuid4()
    execution, _, gateway, user_id = _run(
        [
            ProviderResponse(
                tool_calls=(
                    _call(
                        "call_1",
                        "create_task",
                        project_id=str(project_id),
                        title="Study",
                    ),
                )
            ),
            ProviderResponse(output_text=FINAL_RESULT),
        ],
        writable=True,
    )
    assert execution.trace.tool_count == 1
    assert gateway.users == [user_id]


@pytest.mark.parametrize(
    "response",
    [
        ProviderResponse(output_text=FINAL_RESULT, tool_calls=(_call("call_1"),)),
        ProviderResponse(tool_calls=(_call("same"), _call("same"))),
        ProviderResponse(
            tool_calls=tuple(
                _call(f"c{i}") for i in range(MAX_TOOL_CALLS_PER_RESPONSE + 1)
            )
        ),
        ProviderResponse(tool_calls=(_call("call_1", "unknown_tool"),)),
        ProviderResponse(tool_calls=(_call("call_1", page=0),)),
    ],
)
def test_invalid_or_ambiguous_response_fails_safely_before_tool_use(
    response: ProviderResponse,
) -> None:
    provider = ScriptedProvider([response])
    gateway = FakeGateway()
    with pytest.raises(
        AgentPlanningUnavailableError, match=AGENT_PLANNING_UNAVAILABLE_MESSAGE
    ):
        run_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="synthetic-model",
            provider=provider,
            gateway=gateway,
        )
    assert gateway.users == []


def test_duplicate_call_id_across_rounds_is_rejected() -> None:
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=(_call("same"),)),
            ProviderResponse(tool_calls=(_call("same"),)),
        ]
    )
    gateway = FakeGateway()
    with pytest.raises(AgentPlanningUnavailableError):
        run_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=provider,
            gateway=gateway,
        )
    assert len(gateway.users) == 1


def test_exact_round_and_tool_limits_can_succeed() -> None:
    responses = [
        ProviderResponse(tool_calls=tuple(_call(f"c{i}") for i in range(3))),
        ProviderResponse(tool_calls=tuple(_call(f"c{i}") for i in range(3, 6))),
        ProviderResponse(
            tool_calls=tuple(_call(f"c{i}") for i in range(6, MAX_TOTAL_TOOL_CALLS))
        ),
        ProviderResponse(output_text=FINAL_RESULT),
    ]
    execution, provider, gateway, _ = _run(responses)
    assert execution.trace.round_count == MAX_MODEL_ROUNDS
    assert execution.trace.tool_count == MAX_TOTAL_TOOL_CALLS
    assert len(provider.requests) == MAX_MODEL_ROUNDS
    assert len(gateway.users) == MAX_TOTAL_TOOL_CALLS


def test_missing_final_result_stops_after_exact_round_limit() -> None:
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=(_call(f"c{i}"),))
            for i in range(MAX_MODEL_ROUNDS)
        ]
    )
    with pytest.raises(AgentPlanningUnavailableError):
        run_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=provider,
            gateway=FakeGateway(),
        )
    assert len(provider.requests) == MAX_MODEL_ROUNDS


def test_total_tool_limit_is_checked_before_excess_batch_executes() -> None:
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=tuple(_call(f"c{i}") for i in range(3))),
            ProviderResponse(tool_calls=tuple(_call(f"c{i}") for i in range(3, 6))),
            ProviderResponse(tool_calls=tuple(_call(f"c{i}") for i in range(6, 9))),
        ]
    )
    gateway = FakeGateway()
    with pytest.raises(AgentPlanningUnavailableError):
        run_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=provider,
            gateway=gateway,
        )
    assert len(gateway.users) == 6


def test_provider_and_tool_failures_are_safe_and_not_retried() -> None:
    sensitive = "synthetic-sensitive-diagnostic"
    with pytest.raises(AgentPlanningUnavailableError) as provider_error:
        _run([ProviderTransientError(sensitive)])
    assert sensitive not in str(provider_error.value)

    calls = 0

    def failing_dispatcher(*args: object, **kwargs: object) -> PublicTask:
        nonlocal calls
        calls += 1
        raise RuntimeError(sensitive)

    with pytest.raises(AgentPlanningUnavailableError) as tool_error:
        from app.agent.loop import _run_agent

        _run_agent(
            {"objective": "Plan"},
            context=AgentRuntimeContext(user_id=uuid4()),
            model="model",
            provider=ScriptedProvider([ProviderResponse(tool_calls=(_call("c1"),))]),
            gateway=FakeGateway(),
            dispatcher=failing_dispatcher,
        )
    assert calls == 1
    assert sensitive not in str(tool_error.value)


def test_safe_execution_metadata_round_trips_without_sensitive_payloads() -> None:
    execution, _, _, user_id = _run(
        [
            ProviderResponse(tool_calls=(_call("c1"),)),
            ProviderResponse(output_text=FINAL_RESULT),
        ]
    )
    serialized = execution.model_dump_json()
    assert AgentLoopExecution.model_validate_json(serialized) == execution
    for forbidden in (str(user_id), "objective", "arguments", '"output":'):
        assert forbidden not in serialized
