"""Offline contract tests for least-authority Agent Task write tools."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.context import AgentRuntimeContext
from app.agent.tools import (
    AGENT_TOOL_INPUT_MESSAGE,
    AGENT_TOOL_NOT_ALLOWED_MESSAGE,
    TOOL_DEFINITIONS,
    AgentToolInputError,
    AgentToolNotAllowedError,
    CreateTaskToolArguments,
    UpdateTaskToolArguments,
    available_tool_definitions,
    execute_tool,
)
from app.core.exceptions import TASK_NOT_FOUND_MESSAGE, TaskNotFoundError
from app.models import TaskPriority, TaskStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.agent_domain import AgentDomainGateway


def public_task(
    *, task_id: UUID | None = None, project_id: UUID | None = None
) -> PublicTask:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    return PublicTask(
        id=task_id or uuid4(),
        project_id=project_id or uuid4(),
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


class FakeToolGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, object]] = []

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        self.calls.append(("create_task", user_id, task_input))
        return public_task(project_id=task_input.project_id)

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        self.calls.append(("update_task", user_id, (task_id, task_update)))
        result = public_task(task_id=task_id)
        if task_update.title is not None:
            result = result.model_copy(update={"title": task_update.title})
        return result


def test_write_enabled_allowlist_is_exact_and_read_only_context_hides_writes() -> None:
    read_only = AgentRuntimeContext(user_id=uuid4())
    writable = AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True)

    assert tuple(item.name for item in available_tool_definitions(read_only)) == (
        "list_projects",
        "list_tasks",
    )
    assert available_tool_definitions(writable) == TOOL_DEFINITIONS
    assert tuple(item.name for item in TOOL_DEFINITIONS) == (
        "list_projects",
        "list_tasks",
        "create_task",
        "update_task",
        "batch_create_tasks",
        "delete_task",
    )


def test_write_schemas_exactly_reuse_public_edit_fields_without_owner() -> None:
    assert set(CreateTaskToolArguments.model_fields) == set(TaskCreate.model_fields)
    assert set(UpdateTaskToolArguments.model_fields) == {
        *TaskUpdate.model_fields,
        "task_id",
    }
    write_definitions = {
        definition.name: definition for definition in TOOL_DEFINITIONS[2:]
    }
    write_properties: dict[str, set[str]] = {}
    for name, definition in write_definitions.items():
        properties = definition.input_schema["properties"]
        assert isinstance(properties, dict)
        write_properties[name] = set(properties)
    for forbidden in ("user_id", "created_at", "updated_at", "completed_at"):
        assert all(
            forbidden not in properties for properties in write_properties.values()
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            CreateTaskToolArguments,
            {"project_id": str(uuid4()), "title": "Task", "user_id": str(uuid4())},
        ),
        (UpdateTaskToolArguments, {"task_id": str(uuid4())}),
        (
            UpdateTaskToolArguments,
            {"task_id": str(uuid4()), "created_at": "2026-09-03T00:00:00Z"},
        ),
        (
            UpdateTaskToolArguments,
            {"task_id": str(uuid4()), "status": "COMPLETED"},
        ),
        (
            CreateTaskToolArguments,
            {
                "project_id": str(uuid4()),
                "title": "Task",
                "planned_date": "2026-09-04",
                "due_at": "2026-09-03T00:00:00Z",
            },
        ),
    ],
)
def test_write_arguments_reject_internal_empty_state_and_date_violations(
    model: type[CreateTaskToolArguments] | type[UpdateTaskToolArguments],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_write_permission_is_checked_before_argument_or_gateway_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.tools as tools

    monkeypatch.setattr(
        tools,
        "AgentDomainGateway",
        lambda: pytest.fail("disabled writes must not create a gateway"),
    )
    secret_argument = "private-owner-value"
    with pytest.raises(
        AgentToolNotAllowedError,
        match=AGENT_TOOL_NOT_ALLOWED_MESSAGE,
    ) as exc_info:
        execute_tool(
            "create_task",
            {"user_id": secret_argument},
            AgentRuntimeContext(user_id=uuid4()),
        )

    assert secret_argument not in str(exc_info.value)


def test_invalid_writable_arguments_fail_safely_before_gateway_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.tools as tools

    monkeypatch.setattr(
        tools,
        "AgentDomainGateway",
        lambda: pytest.fail("invalid arguments must not create a gateway"),
    )
    with pytest.raises(AgentToolInputError, match=AGENT_TOOL_INPUT_MESSAGE):
        execute_tool(
            "update_task",
            {"task_id": str(uuid4())},
            AgentRuntimeContext(user_id=uuid4(), write_tools_enabled=True),
        )


def test_create_and_update_forward_trusted_identity_and_public_values() -> None:
    user_id, project_id, task_id = uuid4(), uuid4(), uuid4()
    context = AgentRuntimeContext(user_id=user_id, write_tools_enabled=True)
    gateway = FakeToolGateway()

    created = execute_tool(
        "create_task",
        {"project_id": str(project_id), "title": "  Study  "},
        context,
        gateway=gateway,
    )
    updated = execute_tool(
        "update_task",
        {"task_id": str(task_id), "title": "  Revised  ", "description": None},
        context,
        gateway=gateway,
    )

    create_input = gateway.calls[0][2]
    update_call = gateway.calls[1][2]
    assert isinstance(update_call, tuple)
    update_id, update_input = update_call
    assert gateway.calls[0][:2] == ("create_task", user_id)
    assert isinstance(create_input, TaskCreate)
    assert create_input.title == "Study"
    assert gateway.calls[1][:2] == ("update_task", user_id)
    assert update_id == task_id
    assert isinstance(update_input, TaskUpdate)
    assert update_input.model_dump(exclude_unset=True) == {
        "title": "Revised",
        "description": None,
    }
    assert isinstance(created, PublicTask)
    assert isinstance(updated, PublicTask)
    assert "user_id" not in created.model_dump_json()
    assert "user_id" not in updated.model_dump_json()


def test_gateway_leaves_write_transaction_to_service_and_closes_session() -> None:
    sessions = [MagicMock(spec=Session), MagicMock(spec=Session)]
    first_session, second_session = sessions
    user_id, project_id, task_id = uuid4(), uuid4(), uuid4()
    calls: list[str] = []

    def create_service(
        task_input: TaskCreate,
        received_user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        calls.append("create")
        assert received_user_id == user_id
        assert received_session is first_session
        received_session.commit()
        return public_task(project_id=task_input.project_id)

    def update_service(
        received_task_id: UUID,
        task_update: TaskUpdate,
        received_user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        calls.append("update")
        assert received_task_id == task_id
        assert received_user_id == user_id
        assert received_session is second_session
        received_session.commit()
        return public_task(task_id=task_id, project_id=project_id)

    gateway = AgentDomainGateway(
        session_factory=lambda: sessions.pop(0),
        create_task_service=create_service,
        update_task_service=update_service,
    )
    gateway.create_task(
        user_id=user_id,
        task_input=TaskCreate(project_id=project_id, title="Task"),
    )
    gateway.update_task(
        user_id=user_id,
        task_id=task_id,
        task_update=TaskUpdate(title="New"),
    )

    assert calls == ["create", "update"]
    for session in (first_session, second_session):
        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
        session.close.assert_called_once_with()


def test_write_failure_is_not_retried_and_session_is_closed() -> None:
    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled write failure")
    calls = 0

    def failing_service(
        task_input: TaskCreate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        nonlocal calls
        calls += 1
        received_session.rollback()
        raise failure

    gateway = AgentDomainGateway(
        session_factory=lambda: session,
        create_task_service=failing_service,
    )
    with pytest.raises(RuntimeError) as exc_info:
        gateway.create_task(
            user_id=uuid4(),
            task_input=TaskCreate(project_id=uuid4(), title="Task"),
        )

    assert exc_info.value is failure
    assert calls == 1
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    session.close.assert_called_once_with()


def test_missing_and_foreign_task_share_safe_error_and_close_session() -> None:
    session = MagicMock(spec=Session)
    calls = 0

    def missing_service(
        task_id: UUID,
        task_update: TaskUpdate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        nonlocal calls
        calls += 1
        raise TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)

    gateway = AgentDomainGateway(
        session_factory=lambda: session,
        update_task_service=missing_service,
    )
    for kind in ("missing", "foreign"):
        with pytest.raises(TaskNotFoundError, match=TASK_NOT_FOUND_MESSAGE):
            gateway.update_task(
                user_id=uuid4(),
                task_id=uuid4(),
                task_update=TaskUpdate(title="New"),
            )
        assert kind in {"missing", "foreign"}

    assert calls == 2
    assert session.close.call_count == 2
    session.commit.assert_not_called()


def test_tool_source_has_no_persistence_http_or_retry_primitive() -> None:
    source = Path("app/agent/tools.py").read_text(encoding="utf-8")

    for forbidden in (
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "fastapi",
        "HTTPException",
        "sleep(",
    ):
        assert forbidden not in source
