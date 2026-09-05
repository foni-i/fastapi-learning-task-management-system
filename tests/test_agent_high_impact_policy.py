"""Deterministic capability and Tool-boundary tests for Task 10.6."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.agent.context import AgentRuntimeContext
from app.agent.policy import HIGH_IMPACT_TOOL_NAMES, is_high_impact_tool
from app.agent.tools import (
    AGENT_TOOL_INPUT_MESSAGE,
    AGENT_TOOL_NOT_ALLOWED_MESSAGE,
    TOOL_DEFINITIONS,
    AgentToolGateway,
    AgentToolInputError,
    AgentToolNotAllowedError,
    BatchCreateTasksToolArguments,
    DeleteTaskToolArguments,
    execute_tool,
    validate_tool_arguments,
)
from app.schemas.agent_tool import AgentToolMutationResult
from app.schemas.task import TaskCreate

ROOT = Path(__file__).resolve().parents[1]


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def batch_create_tasks(
        self, *, user_id: UUID, task_inputs: tuple[TaskCreate, ...]
    ) -> AgentToolMutationResult:
        self.calls.append(("batch_create_tasks", (user_id, task_inputs)))
        return AgentToolMutationResult(
            operation="batch_create_tasks",
            reference_task_id=uuid4(),
            affected_count=len(task_inputs),
            summary=f"Created {len(task_inputs)} tasks",
        )

    def delete_task(self, *, user_id: UUID, task_id: UUID) -> AgentToolMutationResult:
        self.calls.append(("delete_task", (user_id, task_id)))
        return AgentToolMutationResult(
            operation="delete_task",
            reference_task_id=task_id,
            affected_count=1,
            summary="Task deleted",
        )


def _task(project_id: UUID, position: int) -> dict[str, object]:
    return {
        "project_id": str(project_id),
        "title": f"Task {position}",
        "priority": "MEDIUM",
    }


def test_high_impact_policy_and_expanded_allowlist_are_exact_and_code_owned() -> None:
    assert frozenset({"batch_create_tasks", "delete_task"}) == HIGH_IMPACT_TOOL_NAMES
    assert is_high_impact_tool("batch_create_tasks") is True
    assert is_high_impact_tool("delete_task") is True
    assert is_high_impact_tool("create_task") is False
    assert tuple(item.name for item in TOOL_DEFINITIONS) == (
        "list_projects",
        "list_tasks",
        "create_task",
        "update_task",
        "batch_create_tasks",
        "delete_task",
    )
    definitions = {item.name: item for item in TOOL_DEFINITIONS}
    for name in HIGH_IMPACT_TOOL_NAMES:
        schema_text = str(definitions[name].input_schema)
        for forbidden in (
            "user_id",
            "approved",
            "approval",
            "session",
            "transaction",
            "commit",
            "rollback",
            "idempotency",
        ):
            assert forbidden not in schema_text


@pytest.mark.parametrize("count", [1, 10])
def test_batch_arguments_accept_exact_bounds_and_one_project(count: int) -> None:
    project_id = uuid4()
    parsed = BatchCreateTasksToolArguments.model_validate(
        {"tasks": [_task(project_id, index) for index in range(count)]}
    )
    assert len(parsed.tasks) == count
    assert {item.project_id for item in parsed.tasks} == {project_id}


@pytest.mark.parametrize("count", [0, 11])
def test_batch_arguments_reject_outside_bounds_without_echoing_items(
    count: int,
) -> None:
    marker = "sensitive-item-marker"
    project_id = uuid4()
    items = [_task(project_id, index) for index in range(count)]
    if items:
        items[-1]["description"] = marker
    with pytest.raises(AgentToolInputError) as error:
        validate_tool_arguments(
            "batch_create_tasks",
            {"tasks": items},
            AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
        )
    assert str(error.value) == AGENT_TOOL_INPUT_MESSAGE
    assert marker not in str(error.value)


def test_batch_rejects_multiple_projects_and_delete_rejects_extra_fields() -> None:
    context = AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True)
    with pytest.raises(AgentToolInputError):
        validate_tool_arguments(
            "batch_create_tasks",
            {"tasks": [_task(uuid4(), 1), _task(uuid4(), 2)]},
            context,
        )
    with pytest.raises(AgentToolInputError):
        validate_tool_arguments(
            "delete_task",
            {"task_id": str(uuid4()), "user_id": str(uuid4())},
            context,
        )
    assert set(DeleteTaskToolArguments.model_fields) == {"task_id"}


def test_write_disabled_rejects_before_gateway_and_enabled_delegates_identity() -> None:
    owner_id = uuid4()
    project_id = uuid4()
    gateway = RecordingGateway()
    with pytest.raises(AgentToolNotAllowedError) as error:
        execute_tool(
            "delete_task",
            {"task_id": str(uuid4())},
            AgentRuntimeContext(user_id=owner_id),
            gateway=cast(AgentToolGateway, gateway),
        )
    assert str(error.value) == AGENT_TOOL_NOT_ALLOWED_MESSAGE
    assert gateway.calls == []

    context = AgentRuntimeContext(user_id=owner_id, write_tools_enabled=True)
    batch = execute_tool(
        "batch_create_tasks",
        {"tasks": [_task(project_id, 1), _task(project_id, 2)]},
        context,
        gateway=cast(AgentToolGateway, gateway),
    )
    task_id = uuid4()
    deleted = execute_tool(
        "delete_task",
        {"task_id": str(task_id)},
        context,
        gateway=cast(AgentToolGateway, gateway),
    )
    assert isinstance(batch, AgentToolMutationResult)
    assert isinstance(deleted, AgentToolMutationResult)
    assert batch.affected_count == 2
    assert deleted.reference_task_id == task_id
    assert gateway.calls[0][1][0] == owner_id  # type: ignore[index]
    assert gateway.calls[1] == ("delete_task", (owner_id, task_id))


def test_tool_and_policy_modules_have_no_persistence_or_http_dependencies() -> None:
    for relative in ("app/agent/tools.py", "app/agent/policy.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source
        assert "app.repositories" not in source
        assert "fastapi" not in source
