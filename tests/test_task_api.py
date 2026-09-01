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
    TASK_TRANSITION_MESSAGE,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.db.session import get_session
from app.main import app
from app.models import TaskPriority, TaskStatus, User
from app.schemas.task import (
    TASK_DATE_ORDER_MESSAGE,
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)

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
    *,
    task_id: UUID | None = None,
    project_id: UUID | None = None,
    status: TaskStatus = TaskStatus.TODO,
) -> PublicTask:
    timestamp = datetime(2026, 9, 1, tzinfo=UTC)
    completed_at = timestamp if status is TaskStatus.COMPLETED else None
    return PublicTask(
        id=task_id or uuid4(),
        project_id=project_id or uuid4(),
        title="Study",
        description=None,
        status=status,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=30,
        completed_at=completed_at,
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
            client.patch(f"/api/v1/tasks/{uuid4()}", json={"title": "New"}),
            client.post(f"/api/v1/tasks/{uuid4()}/complete"),
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
        assert response.headers["www-authenticate"] == "Bearer"


def test_update_task_delegates_missing_null_owner_and_same_session(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = task_request_context
    task_id = uuid4()
    observed: dict[str, object] = {}

    def fake_update(
        received_task_id: UUID,
        update: TaskUpdate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        observed.update(
            task_id=received_task_id,
            update=update,
            user_id=user_id,
            session=received_session,
        )
        return make_public_task(task_id=received_task_id)

    monkeypatch.setattr(tasks, "update_owned_task", fake_update)
    response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"description": None, "due_at": None, "status": "IN_PROGRESS"},
    )
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    update = observed["update"]
    assert isinstance(update, TaskUpdate)
    assert update.model_fields_set == {"description", "due_at", "status"}
    assert observed["task_id"] == task_id
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": str(uuid4())},
        {"user_id": str(uuid4())},
        {"project_id": str(uuid4())},
        {"completed_at": "2026-09-01T00:00:00Z"},
        {"created_at": "2026-09-01T00:00:00Z"},
        {"title": None},
        {"priority": None},
        {"status": None},
        {"status": "COMPLETED"},
    ],
)
def test_update_task_rejects_invalid_or_internal_payload_before_service(
    payload: dict[str, object],
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    monkeypatch.setattr(tasks, "update_owned_task", service)
    response = client.patch(f"/api/v1/tasks/{uuid4()}", json=payload)
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (TaskDateOrderError(TASK_DATE_ORDER_MESSAGE), TASK_DATE_ORDER_MESSAGE),
        (TaskTransitionError(TASK_TRANSITION_MESSAGE), TASK_TRANSITION_MESSAGE),
    ],
)
def test_update_task_maps_safe_domain_errors_to_422(
    error: Exception,
    detail: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "update_owned_task", MagicMock(side_effect=error))
    response = client.patch(f"/api/v1/tasks/{uuid4()}", json={"title": "New"})
    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("target", ["TODO", "IN_PROGRESS", "CANCELLED"])
def test_update_task_returns_reopened_public_state(
    target: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()

    def fake_update(
        received_task_id: UUID,
        update: TaskUpdate,
        _user_id: UUID,
        _session: Session,
    ) -> PublicTask:
        assert received_task_id == task_id
        assert update.status is TaskStatus(target)
        return make_public_task(task_id=task_id, status=TaskStatus(target))

    monkeypatch.setattr(tasks, "update_owned_task", fake_update)
    response = client.patch(f"/api/v1/tasks/{task_id}", json={"status": target})
    assert response.status_code == 200
    assert response.json()["status"] == target
    assert response.json()["completed_at"] is None


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_update_task_hides_missing_and_foreign_with_same_404(
    kind: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "update_owned_task",
        MagicMock(side_effect=TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)),
    )
    response = client.patch(f"/api/v1/tasks/{uuid4()}", json={"title": "New"})
    assert kind in {"missing", "foreign"}
    assert response.status_code == 404
    assert response.json() == {"detail": TASK_NOT_FOUND_MESSAGE}


def test_complete_task_delegates_owner_and_same_session(
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = task_request_context
    task_id = uuid4()
    observed: dict[str, object] = {}

    def fake_complete(
        received_task_id: UUID,
        user_id: UUID,
        received_session: Session,
    ) -> PublicTask:
        observed.update(
            task_id=received_task_id,
            user_id=user_id,
            session=received_session,
        )
        return make_public_task(task_id=received_task_id, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(tasks, "complete_owned_task", fake_complete)
    response = client.post(f"/api/v1/tasks/{task_id}/complete")
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert response.json()["status"] == "COMPLETED"
    assert observed == {"task_id": task_id, "user_id": user.id, "session": session}
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("operation", ["patch", "complete"])
def test_lifecycle_routes_reject_malformed_uuid_before_service(
    operation: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    if operation == "patch":
        monkeypatch.setattr(tasks, "update_owned_task", service)
        response = client.patch("/api/v1/tasks/not-a-uuid", json={"title": "New"})
    else:
        monkeypatch.setattr(tasks, "complete_owned_task", service)
        response = client.post("/api/v1/tasks/not-a-uuid/complete")
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_complete_task_hides_missing_and_foreign_with_same_404(
    kind: str,
    client: TestClient,
    task_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "complete_owned_task",
        MagicMock(side_effect=TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)),
    )
    response = client.post(f"/api/v1/tasks/{uuid4()}/complete")
    assert kind in {"missing", "foreign"}
    assert response.status_code == 404
    assert response.json() == {"detail": TASK_NOT_FOUND_MESSAGE}
