"""Offline contract tests for owner-scoped Agent read tools."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.context import AgentRuntimeContext
from app.agent.tools import (
    AGENT_TOOL_INPUT_MESSAGE,
    AGENT_TOOL_NOT_ALLOWED_MESSAGE,
    READ_TOOL_DEFINITIONS,
    AgentToolInputError,
    AgentToolNotAllowedError,
    ListProjectsToolArguments,
    ListTasksToolArguments,
    available_tool_definitions,
    execute_tool,
)
from app.models import ProjectStatus
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.agent_domain import AgentDomainGateway


class FakeReadGateway:
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
        self.calls.append(
            ("list_projects", user_id, (page, page_size, include_archived))
        )
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        self.calls.append(("list_tasks", user_id, query))
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        pytest.fail("read tests must not create tasks")

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        pytest.fail("read tests must not update tasks")


def test_read_allowlist_and_definitions_are_exact_and_deterministic() -> None:
    context = AgentRuntimeContext(user_id=uuid4())
    first = available_tool_definitions(context)
    second = available_tool_definitions(context)

    assert first == second == READ_TOOL_DEFINITIONS
    assert tuple(definition.name for definition in first) == (
        "list_projects",
        "list_tasks",
        "search_knowledge",
    )
    assert all(definition.description for definition in first)
    assert all(
        definition.input_schema.get("additionalProperties") is False
        for definition in first
    )
    serialized = "".join(definition.model_dump_json() for definition in first)
    assert "user_id" not in serialized
    assert "session" not in serialized.lower()


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ListProjectsToolArguments, {"user_id": str(uuid4())}),
        (ListTasksToolArguments, {"user_id": str(uuid4())}),
        (ListProjectsToolArguments, {"page": 0}),
        (ListProjectsToolArguments, {"page_size": 101}),
        (ListTasksToolArguments, {"sort_by": "user_id"}),
        (ListTasksToolArguments, {"unexpected": True}),
    ],
)
def test_read_arguments_reject_identity_extra_and_unbounded_values(
    model: type[ListProjectsToolArguments] | type[ListTasksToolArguments],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_execute_read_tools_forwards_only_trusted_identity_and_public_query() -> None:
    user_id = uuid4()
    context = AgentRuntimeContext(user_id=user_id)
    gateway = FakeReadGateway()

    projects = execute_tool(
        "list_projects",
        {"page": 2, "page_size": 10, "include_archived": True},
        context,
        gateway=gateway,
    )
    tasks = execute_tool(
        "list_tasks",
        {"page": 3, "page_size": 5, "status": "TODO", "overdue": True},
        context,
        gateway=gateway,
    )

    assert projects.model_dump() == {
        "items": [],
        "page": 2,
        "page_size": 10,
        "total": 0,
        "pages": 0,
    }
    assert isinstance(tasks, TaskListResponse)
    assert gateway.calls[0] == ("list_projects", user_id, (2, 10, True))
    task_query = cast(TaskListQuery, gateway.calls[1][2])
    assert gateway.calls[1][:2] == ("list_tasks", user_id)
    assert (task_query.page, task_query.page_size, task_query.overdue) == (3, 5, True)


@pytest.mark.parametrize(
    ("name", "arguments", "error", "message"),
    [
        (
            "unknown_tool",
            {},
            AgentToolNotAllowedError,
            AGENT_TOOL_NOT_ALLOWED_MESSAGE,
        ),
        (
            "list_projects",
            {"user_id": "private-owner"},
            AgentToolInputError,
            AGENT_TOOL_INPUT_MESSAGE,
        ),
    ],
)
def test_invalid_request_fails_safely_before_gateway_creation(
    name: str,
    arguments: dict[str, object],
    error: type[Exception],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.tools as tools

    monkeypatch.setattr(
        tools,
        "AgentDomainGateway",
        lambda: pytest.fail("invalid tool input must not create a gateway"),
    )
    with pytest.raises(error, match=message) as exc_info:
        execute_tool(name, arguments, AgentRuntimeContext(user_id=uuid4()))

    assert "private-owner" not in str(exc_info.value)
    assert "unknown_tool" not in str(exc_info.value)


def test_domain_gateway_closes_each_read_session_without_committing() -> None:
    sessions = [MagicMock(spec=Session), MagicMock(spec=Session)]
    user_id = uuid4()
    project_calls: list[tuple[UUID, Session]] = []
    task_calls: list[tuple[TaskListQuery, UUID, Session]] = []

    def list_projects_service(
        received_user_id: UUID,
        session: Session,
        *,
        page: int = 1,
        page_size: int = 20,
        include_archived: bool = False,
    ) -> ProjectListResponse:
        project_calls.append((received_user_id, session))
        assert (page, page_size, include_archived) == (1, 20, False)
        return ProjectListResponse(items=[], page=1, page_size=20, total=0, pages=0)

    def list_tasks_service(
        query: TaskListQuery,
        received_user_id: UUID,
        session: Session,
    ) -> TaskListResponse:
        task_calls.append((query, received_user_id, session))
        return TaskListResponse(items=[], page=1, page_size=20, total=0, pages=0)

    gateway = AgentDomainGateway(
        session_factory=lambda: sessions.pop(0),
        list_projects_service=list_projects_service,
        list_tasks_service=list_tasks_service,
    )
    first_session, second_session = sessions

    gateway.list_projects(user_id=user_id, page=1, page_size=20, include_archived=False)
    gateway.list_tasks(user_id=user_id, query=TaskListQuery())

    assert project_calls == [(user_id, first_session)]
    assert task_calls[0][1:] == (user_id, second_session)
    for session in (first_session, second_session):
        session.close.assert_called_once_with()
        session.commit.assert_not_called()
        session.rollback.assert_not_called()


def test_domain_gateway_closes_session_when_read_service_raises() -> None:
    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled service failure")

    def failing_service(
        user_id: UUID,
        received_session: Session,
        *,
        page: int = 1,
        page_size: int = 20,
        include_archived: bool = False,
    ) -> ProjectListResponse:
        raise failure

    gateway = AgentDomainGateway(
        session_factory=lambda: session,
        list_projects_service=failing_service,
    )
    with pytest.raises(RuntimeError) as exc_info:
        gateway.list_projects(
            user_id=uuid4(), page=1, page_size=20, include_archived=False
        )

    assert exc_info.value is failure
    session.close.assert_called_once_with()
    session.commit.assert_not_called()


def test_read_gateway_keeps_two_runtime_identities_isolated() -> None:
    sessions = [MagicMock(spec=Session), MagicMock(spec=Session)]
    first_user, second_user = uuid4(), uuid4()
    received_users: list[UUID] = []

    def owner_scoped_service(
        user_id: UUID,
        session: Session,
        *,
        page: int = 1,
        page_size: int = 20,
        include_archived: bool = False,
    ) -> ProjectListResponse:
        received_users.append(user_id)
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    gateway = AgentDomainGateway(
        session_factory=lambda: sessions.pop(0),
        list_projects_service=owner_scoped_service,
    )
    gateway.list_projects(
        user_id=first_user, page=1, page_size=20, include_archived=False
    )
    gateway.list_projects(
        user_id=second_user, page=1, page_size=20, include_archived=False
    )

    assert received_users == [first_user, second_user]


def test_tool_module_has_no_persistence_or_http_imports() -> None:
    source = Path("app/agent/tools.py").read_text(encoding="utf-8")

    for forbidden in (
        "sqlalchemy",
        "app.db",
        "app.repositories",
        "fastapi",
        "HTTPException",
    ):
        assert forbidden not in source


def test_runtime_context_is_immutable_and_not_a_model_schema() -> None:
    context = AgentRuntimeContext(user_id=uuid4())

    with pytest.raises((AttributeError, TypeError)):
        context.write_tools_enabled = True  # type: ignore[misc]
    assert not hasattr(context, "model_json_schema")


def test_gateway_public_results_do_not_expose_owner_or_orm_values() -> None:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    public = ProjectListResponse.model_validate(
        {
            "items": [
                {
                    "id": str(uuid4()),
                    "name": "Study",
                    "description": None,
                    "start_date": None,
                    "target_date": None,
                    "status": ProjectStatus.NOT_STARTED,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "page": 1,
            "page_size": 20,
            "total": 1,
            "pages": 1,
        }
    )

    assert "user_id" not in public.model_dump_json()
    assert "_sa_instance_state" not in public.model_dump_json()
