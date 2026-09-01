"""Connection-free API tests for authenticated Task endpoints."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import tasks
from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    ProjectNotFoundError,
    TaskNotFoundError,
)
from app.db.session import get_session
from app.main import app
from app.models import TaskPriority, TaskStatus, User
from app.schemas.task import PublicTask, TaskCreate, TaskListQuery, TaskListResponse

PUBLIC_FIELDS = {
    "id",
    "project_id",
    "title",
    "description",
    "status",
    "priority",
    "planned_date",
    "due_at",
    "estimated_minutes",
    "completed_at",
    "created_at",
    "updated_at",
}


def make_user() -> User:
    timestamp = datetime(2026, 9, 1, tzinfo=UTC)
    return cast(
        User,
        SimpleNamespace(
            id=uuid4(),
            email="task-owner@example.com",
            password_hash="synthetic-test-hash",
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def make_public_task(
    *, task_id: UUID | None = None, project_id: UUID | None = None
) -> PublicTask:
    timestamp = datetime(2026, 9, 1, tzinfo=UTC)
    return PublicTask(
        id=task_id or uuid4(),
        project_id=project_id or uuid4(),
        title="Study",
        description=None,
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=30,
        completed_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.fixture
def task_request_context() -> Iterator[tuple[MagicMock, User, list[str]]]:
    session = MagicMock(spec=Session)
    user = make_user()
    lifecycle: list[str] = []

    def override_session() -> Iterator[Session]:
        lifecycle.append("opened")
        try:
            yield session
        finally:
            lifecycle.append("closed")

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield session, user, lifecycle
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_create_task_delegates_canonical_input_owner_and_same_session(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = task_request_context
    project_id = uuid4()
    observed: dict[str, object] = {}

    def fake_create(
        task_input: TaskCreate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        observed.update(input=task_input, user_id=user_id, session=received_session)
        return make_public_task(project_id=task_input.project_id)

    monkeypatch.setattr(tasks, "create_task", fake_create)
    response = client.post(
        "/api/v1/tasks",
        json={
            "project_id": str(project_id),
            "title": "  Study  ",
            "description": "   ",
            "estimated_minutes": 30,
        },
    )

    assert response.status_code == 201
    assert set(response.json()) == PUBLIC_FIELDS
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    task_input = observed["input"]
    assert isinstance(task_input, TaskCreate)
    assert (task_input.title, task_input.description) == ("Study", None)
    assert "user_id" not in response.text
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("field", ["user_id", "status", "completed_at"])
def test_create_task_rejects_internal_fields_before_service(
    field: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    monkeypatch.setattr(tasks, "create_task", service)
    response = client.post(
        "/api/v1/tasks",
        json={"project_id": str(uuid4()), "title": "Task", field: "forbidden"},
    )
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_create_task_hides_missing_and_foreign_project_with_same_404(
    kind: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "create_task",
        MagicMock(side_effect=ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)),
    )
    response = client.post(
        "/api/v1/tasks",
        json={"project_id": str(uuid4()), "title": "Task"},
    )
    assert kind in {"missing", "foreign"}
    assert response.status_code == 404
    assert response.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}


def test_task_detail_delegates_resource_owner_and_same_session(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = task_request_context
    task_id = uuid4()
    observed: dict[str, object] = {}

    def fake_get(
        received_task_id: UUID,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        observed.update(
            task_id=received_task_id,
            user_id=user_id,
            session=received_session,
        )
        return make_public_task(task_id=received_task_id)

    monkeypatch.setattr(tasks, "get_owned_task", fake_get)
    response = client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert observed == {"task_id": task_id, "user_id": user.id, "session": session}
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_task_detail_hides_missing_and_foreign_with_same_404(
    kind: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_owned_task",
        MagicMock(side_effect=TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)),
    )
    response = client.get(f"/api/v1/tasks/{uuid4()}")
    assert kind in {"missing", "foreign"}
    assert response.status_code == 404
    assert response.json() == {"detail": TASK_NOT_FOUND_MESSAGE}


def test_task_detail_rejects_malformed_uuid_before_service(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    monkeypatch.setattr(tasks, "get_owned_task", service)
    response = client.get("/api/v1/tasks/not-a-uuid")
    assert response.status_code == 422
    service.assert_not_called()


def test_task_list_forwards_strict_query_owner_and_same_session(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = task_request_context
    observed: dict[str, object] = {}

    def fake_list(
        query: TaskListQuery,
        user_id: UUID,
        received_session: Session,
    ) -> TaskListResponse:
        observed.update(query=query, user_id=user_id, session=received_session)
        return TaskListResponse(
            items=[make_public_task()],
            page=query.page,
            page_size=query.page_size,
            total=1,
            pages=1,
        )

    monkeypatch.setattr(tasks, "list_owned_tasks", fake_list)
    response = client.get(
        "/api/v1/tasks?page=2&page_size=5&status=TODO&priority=HIGH"
        "&overdue=false&title=%20Study%20&sort_by=title&sort_direction=asc"
    )
    assert response.status_code == 200
    assert set(response.json()) == {"items", "page", "page_size", "total", "pages"}
    assert set(response.json()["items"][0]) == PUBLIC_FIELDS
    query = observed["query"]
    assert isinstance(query, TaskListQuery)
    assert (query.page, query.page_size, query.title) == (2, 5, "Study")
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page_size=101",
        "sort_by=user_id",
        "sort_direction=sideways",
        "planned_from=2026-09-02&planned_to=2026-09-01",
        "unknown=value",
        f"user_id={uuid4()}",
    ],
)
def test_task_list_rejects_invalid_or_unknown_query_before_service(
    query: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    monkeypatch.setattr(tasks, "list_owned_tasks", service)
    response = client.get(f"/api/v1/tasks?{query}")
    assert response.status_code == 422
    service.assert_not_called()


def test_task_routes_require_existing_bearer_challenge(client: TestClient) -> None:
    session = MagicMock(spec=Session)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        responses = (
            client.post(
                "/api/v1/tasks",
                json={"project_id": str(uuid4()), "title": "Task"},
            ),
            client.get("/api/v1/tasks"),
            client.get(f"/api/v1/tasks/{uuid4()}"),
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
        assert response.headers["www-authenticate"] == "Bearer"
