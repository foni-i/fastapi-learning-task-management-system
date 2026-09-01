"""End-to-end Stage 7 Task tests against dedicated PostgreSQL."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import PROJECT_NOT_FOUND_MESSAGE, TASK_NOT_FOUND_MESSAGE
from app.db.session import get_session
from app.main import app
from app.models import Task
from tests.integration.test_authentication import bearer, login, register_user
from tests.integration.test_projects import ProjectHarness, create_project

pytestmark = pytest.mark.integration

TASKS_PATH = "/api/v1/tasks"
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


class TaskHarness(ProjectHarness):
    """Track committed Tasks and preserve exact cleanup ordering."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.engine = engine
        self.task_ids: set[UUID] = set()

    def track_task(self, body: Mapping[str, object]) -> UUID:
        task_id = UUID(str(body["id"]))
        self.task_ids.add(task_id)
        return task_id

    def task_snapshot(self, task_id: UUID) -> SimpleNamespace | None:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return None
            return SimpleNamespace(
                id=task.id,
                user_id=task.user_id,
                project_id=task.project_id,
                title=task.title,
                description=task.description,
                status=task.status,
                priority=task.priority,
                planned_date=task.planned_date,
                due_at=task.due_at,
                estimated_minutes=task.estimated_minutes,
                completed_at=task.completed_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )

    def count_task(self, task_id: UUID) -> int:
        with self.session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(Task).where(Task.id == task_id)
            )
        assert count is not None
        return count

    def cleanup(self) -> None:
        if self.task_ids:
            with self.session_factory.begin() as session:
                session.execute(delete(Task).where(Task.id.in_(self.task_ids)))
        super().cleanup()


@pytest.fixture
def task_harness(integration_engine: Engine) -> Iterator[TaskHarness]:
    harness = TaskHarness(integration_engine)
    app.dependency_overrides[get_session] = harness.session_dependency
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_session, None)
        opened_ids = {id(session) for session in harness.opened_sessions}
        closed_ids = {id(session) for session in harness.closed_sessions}
        assert opened_ids == closed_ids
        harness.cleanup()


@pytest.fixture
def task_client(task_harness: TaskHarness) -> Iterator[TestClient]:
    assert task_harness is not None
    with TestClient(app) as client:
        yield client


def create_task(
    client: TestClient,
    harness: TaskHarness,
    token: str,
    project_id: UUID,
    *,
    title: str,
    description: str | None = None,
    priority: str = "MEDIUM",
    planned_date: str | None = None,
    due_at: str | None = None,
    estimated_minutes: int | None = None,
) -> tuple[UUID, dict[str, object]]:
    payload: dict[str, object] = {
        "project_id": str(project_id),
        "title": title,
        "priority": priority,
    }
    for field, value in {
        "description": description,
        "planned_date": planned_date,
        "due_at": due_at,
        "estimated_minutes": estimated_minutes,
    }.items():
        if value is not None:
            payload[field] = value
    response = client.post(TASKS_PATH, headers=bearer(token), json=payload)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == PUBLIC_FIELDS
    assert "user_id" not in body
    return harness.track_task(body), body


def test_task_crud_lifecycle_deletion_and_owner_isolation(
    task_client: TestClient,
    task_harness: TaskHarness,
) -> None:
    marker = task_harness.session_marker()
    owner_id, owner_email = register_user(
        task_client, task_harness, f"task-owner-{uuid4()}@example.com"
    )
    _other_id, other_email = register_user(
        task_client, task_harness, f"task-other-{uuid4()}@example.com"
    )
    owner_token = login(task_client, owner_email)
    other_token = login(task_client, other_email)
    owner_project, _ = create_project(
        task_client, task_harness, owner_token, name="Owner task project"
    )
    other_project, _ = create_project(
        task_client, task_harness, other_token, name="Foreign task project"
    )

    rejected_project = task_client.post(
        TASKS_PATH,
        headers=bearer(owner_token),
        json={"project_id": str(other_project), "title": "Forbidden"},
    )
    assert rejected_project.status_code == 404
    assert rejected_project.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}

    task_id, created = create_task(
        task_client,
        task_harness,
        owner_token,
        owner_project,
        title="  Lifecycle task  ",
        description="  Clear later  ",
        priority="HIGH",
        planned_date="2026-09-01",
        due_at="2026-09-02T08:00:00+08:00",
        estimated_minutes=45,
    )
    assert created["title"] == "Lifecycle task"
    assert created["description"] == "Clear later"
    assert created["due_at"] == "2026-09-02T00:00:00Z"
    stored = task_harness.task_snapshot(task_id)
    assert stored is not None
    assert stored.user_id == owner_id
    assert stored.project_id == owner_project

    own_detail = task_client.get(f"{TASKS_PATH}/{task_id}", headers=bearer(owner_token))
    assert own_detail.status_code == 200
    assert own_detail.json() == created

    missing_id = uuid4()
    foreign_requests = (
        task_client.get(f"{TASKS_PATH}/{task_id}", headers=bearer(other_token)),
        task_client.patch(
            f"{TASKS_PATH}/{task_id}",
            headers=bearer(other_token),
            json={"title": "Foreign"},
        ),
        task_client.post(
            f"{TASKS_PATH}/{task_id}/complete", headers=bearer(other_token)
        ),
        task_client.delete(f"{TASKS_PATH}/{task_id}", headers=bearer(other_token)),
    )
    missing_requests = (
        task_client.get(f"{TASKS_PATH}/{missing_id}", headers=bearer(other_token)),
        task_client.patch(
            f"{TASKS_PATH}/{missing_id}",
            headers=bearer(other_token),
            json={"title": "Missing"},
        ),
        task_client.post(
            f"{TASKS_PATH}/{missing_id}/complete", headers=bearer(other_token)
        ),
        task_client.delete(f"{TASKS_PATH}/{missing_id}", headers=bearer(other_token)),
    )
    for foreign, missing in zip(foreign_requests, missing_requests, strict=True):
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": TASK_NOT_FOUND_MESSAGE}
    assert task_harness.count_task(task_id) == 1

    changed = task_client.patch(
        f"{TASKS_PATH}/{task_id}",
        headers=bearer(owner_token),
        json={
            "description": None,
            "planned_date": None,
            "due_at": None,
            "estimated_minutes": None,
            "status": "IN_PROGRESS",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["description"] is None
    assert changed.json()["planned_date"] is None
    assert changed.json()["due_at"] is None
    assert changed.json()["estimated_minutes"] is None
    changed_at = changed.json()["updated_at"]

    noop = task_client.patch(
        f"{TASKS_PATH}/{task_id}",
        headers=bearer(owner_token),
        json={"status": "IN_PROGRESS"},
    )
    assert noop.status_code == 200
    assert noop.json()["updated_at"] == changed_at

    completed = task_client.post(
        f"{TASKS_PATH}/{task_id}/complete", headers=bearer(owner_token)
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["completed_at"] == completed.json()["updated_at"]
    repeated = task_client.post(
        f"{TASKS_PATH}/{task_id}/complete", headers=bearer(owner_token)
    )
    assert repeated.status_code == 200
    assert repeated.json()["completed_at"] == completed.json()["completed_at"]
    assert repeated.json()["updated_at"] == completed.json()["updated_at"]

    reopened = task_client.patch(
        f"{TASKS_PATH}/{task_id}",
        headers=bearer(owner_token),
        json={"status": "TODO"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "TODO"
    assert reopened.json()["completed_at"] is None

    deleted = task_client.delete(f"{TASKS_PATH}/{task_id}", headers=bearer(owner_token))
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert task_harness.count_task(task_id) == 0
    assert (
        task_client.get(
            f"{TASKS_PATH}/{task_id}", headers=bearer(owner_token)
        ).status_code
        == 404
    )
    task_harness.assert_sessions_closed_since(marker, 24)


def test_task_pagination_filters_overdue_and_stable_sorting(
    task_client: TestClient,
    task_harness: TaskHarness,
) -> None:
    _owner_id, owner_email = register_user(
        task_client, task_harness, f"task-list-{uuid4()}@example.com"
    )
    _other_id, other_email = register_user(
        task_client, task_harness, f"task-list-other-{uuid4()}@example.com"
    )
    owner_token = login(task_client, owner_email)
    other_token = login(task_client, other_email)
    project_id, _ = create_project(
        task_client, task_harness, owner_token, name="Task filters"
    )
    other_project, _ = create_project(
        task_client, task_harness, other_token, name="Foreign filters"
    )

    literal_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="100%_Focus",
        priority="HIGH",
        planned_date="2020-01-01",
        due_at="2020-01-02T00:00:00Z",
    )
    future_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="Focus Alpha",
        priority="LOW",
        planned_date="2030-01-01",
        due_at="2030-01-02T00:00:00Z",
    )
    completed_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="Completed Focus",
        priority="URGENT",
        planned_date="2020-02-01",
        due_at="2020-02-02T00:00:00Z",
    )
    cancelled_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="Cancelled",
    )
    no_dates_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="No dates",
    )
    create_task(
        task_client,
        task_harness,
        other_token,
        other_project,
        title="Foreign Focus",
    )
    assert (
        task_client.patch(
            f"{TASKS_PATH}/{future_id}",
            headers=bearer(owner_token),
            json={"status": "IN_PROGRESS"},
        ).status_code
        == 200
    )
    assert (
        task_client.post(
            f"{TASKS_PATH}/{completed_id}/complete", headers=bearer(owner_token)
        ).status_code
        == 200
    )
    assert (
        task_client.patch(
            f"{TASKS_PATH}/{cancelled_id}",
            headers=bearer(owner_token),
            json={"status": "CANCELLED"},
        ).status_code
        == 200
    )

    owner_ids = {literal_id, future_id, completed_id, cancelled_id, no_dates_id}
    tied_time = datetime(2026, 9, 1, tzinfo=UTC)
    with task_harness.session_factory.begin() as session:
        session.execute(
            update(Task)
            .where(Task.id.in_(owner_ids))
            .values(created_at=tied_time, updated_at=tied_time)
        )

    pages = [
        task_client.get(
            TASKS_PATH,
            headers=bearer(owner_token),
            params={"page": page, "page_size": 2},
        ).json()
        for page in (1, 2, 3)
    ]
    assert pages[0]["total"] == 5
    assert pages[0]["pages"] == 3
    listed_ids = [UUID(item["id"]) for page in pages for item in page["items"]]
    assert listed_ids == sorted(owner_ids, reverse=True)
    assert len(set(listed_ids)) == 5

    def listed(**params: str | int | float | bool | None) -> set[UUID]:
        response = task_client.get(
            TASKS_PATH, headers=bearer(owner_token), params=params
        )
        assert response.status_code == 200
        return {UUID(item["id"]) for item in response.json()["items"]}

    assert listed(project_id=str(project_id)) == owner_ids
    assert listed(status="COMPLETED") == {completed_id}
    assert listed(priority="HIGH") == {literal_id}
    assert listed(planned_from="2029-01-01") == {future_id}
    assert listed(planned_to="2021-01-01") == {literal_id, completed_id}
    assert listed(due_from="2029-01-01T00:00:00Z") == {future_id}
    assert listed(due_to="2021-01-01T00:00:00Z") == {literal_id, completed_id}
    assert listed(overdue="true") == {literal_id}
    assert listed(overdue="false") == {
        future_id,
        completed_id,
        cancelled_id,
        no_dates_id,
    }
    assert listed(title="focus") == {literal_id, future_id, completed_id}
    assert listed(title="%_") == {literal_id}

    due_sorted = task_client.get(
        TASKS_PATH,
        headers=bearer(owner_token),
        params={"sort_by": "due_at", "sort_direction": "asc"},
    ).json()["items"]
    due_sorted_ids = [UUID(item["id"]) for item in due_sorted]
    assert due_sorted_ids[:3] == [literal_id, completed_id, future_id]
    assert set(due_sorted_ids[-2:]) == {cancelled_id, no_dates_id}

    invalid_queries: tuple[dict[str, str | int], ...] = (
        {"page": 0},
        {"page_size": 101},
        {"sort_by": "user_id"},
        {"sort_direction": "sideways"},
        {"planned_from": "2026-09-02", "planned_to": "2026-09-01"},
        {"due_from": "2026-09-02T00:00:00Z", "due_to": "2026-09-01T00:00:00Z"},
    )
    for params in invalid_queries:
        response = task_client.get(
            TASKS_PATH, headers=bearer(owner_token), params=params
        )
        assert response.status_code == 422
        lowered = response.text.casefold()
        assert "integrityerror" not in lowered
        assert "postgresql+psycopg" not in lowered
        assert "fk_tasks_" not in lowered


def test_task_date_state_machine_and_idempotent_boundaries(
    task_client: TestClient,
    task_harness: TaskHarness,
) -> None:
    _owner_id, owner_email = register_user(
        task_client, task_harness, f"task-state-{uuid4()}@example.com"
    )
    token = login(task_client, owner_email)
    project_id, _ = create_project(task_client, task_harness, token, name="Task states")

    boundary_id, boundary = create_task(
        task_client,
        task_harness,
        token,
        project_id,
        title="Boundary",
        planned_date="2026-09-01",
        due_at="2026-09-01T00:00:00Z",
    )
    noop = task_client.patch(
        f"{TASKS_PATH}/{boundary_id}",
        headers=bearer(token),
        json={"title": "Boundary"},
    )
    assert noop.status_code == 200
    assert noop.json()["updated_at"] == boundary["updated_at"]

    invalid_create = task_client.post(
        TASKS_PATH,
        headers=bearer(token),
        json={
            "project_id": str(project_id),
            "title": "Invalid date",
            "planned_date": "2026-09-01",
            "due_at": "2026-08-31T23:59:59Z",
        },
    )
    assert invalid_create.status_code == 422

    persisted_id, _ = create_task(
        task_client,
        task_harness,
        token,
        project_id,
        title="Persisted date",
        planned_date="2026-09-02",
    )
    invalid_patch = task_client.patch(
        f"{TASKS_PATH}/{persisted_id}",
        headers=bearer(token),
        json={"due_at": "2026-09-01T23:59:59Z"},
    )
    assert invalid_patch.status_code == 422
    persisted = task_harness.task_snapshot(persisted_id)
    assert persisted is not None
    assert persisted.due_at is None

    task_ids: list[UUID] = []
    initial_states = ("TODO", "IN_PROGRESS", "CANCELLED")
    reopen_states = ("TODO", "IN_PROGRESS", "CANCELLED")
    for initial, reopen in zip(initial_states, reopen_states, strict=True):
        task_id, _ = create_task(
            task_client,
            task_harness,
            token,
            project_id,
            title=f"Complete from {initial}",
        )
        task_ids.append(task_id)
        if initial != "TODO":
            changed = task_client.patch(
                f"{TASKS_PATH}/{task_id}",
                headers=bearer(token),
                json={"status": initial},
            )
            assert changed.status_code == 200
        first = task_client.post(
            f"{TASKS_PATH}/{task_id}/complete", headers=bearer(token)
        )
        repeat = task_client.post(
            f"{TASKS_PATH}/{task_id}/complete", headers=bearer(token)
        )
        assert first.status_code == repeat.status_code == 200
        assert first.json()["status"] == "COMPLETED"
        assert first.json()["completed_at"] == first.json()["updated_at"]
        assert repeat.json()["completed_at"] == first.json()["completed_at"]
        assert repeat.json()["updated_at"] == first.json()["updated_at"]
        reopened = task_client.patch(
            f"{TASKS_PATH}/{task_id}",
            headers=bearer(token),
            json={"status": reopen},
        )
        assert reopened.status_code == 200
        assert reopened.json()["status"] == reopen
        assert reopened.json()["completed_at"] is None

    assert (
        task_client.patch(
            f"{TASKS_PATH}/{task_ids[0]}",
            headers=bearer(token),
            json={"status": "COMPLETED"},
        ).status_code
        == 422
    )
    cancelled = task_ids[2]
    assert (
        task_client.patch(
            f"{TASKS_PATH}/{cancelled}",
            headers=bearer(token),
            json={"status": "IN_PROGRESS"},
        ).status_code
        == 422
    )


def test_task_named_constraints_and_composite_ownership_defense(
    task_client: TestClient,
    task_harness: TaskHarness,
) -> None:
    owner_id, owner_email = register_user(
        task_client, task_harness, f"task-constraints-{uuid4()}@example.com"
    )
    other_id, other_email = register_user(
        task_client, task_harness, f"task-constraints-other-{uuid4()}@example.com"
    )
    owner_token = login(task_client, owner_email)
    other_token = login(task_client, other_email)
    project_id, _ = create_project(
        task_client, task_harness, owner_token, name="Task constraints"
    )
    other_project, _ = create_project(
        task_client, task_harness, other_token, name="Other constraints"
    )
    existing_id, _ = create_task(
        task_client,
        task_harness,
        owner_token,
        project_id,
        title="Existing",
    )

    inspector = inspect(task_harness.engine)
    assert inspector.get_pk_constraint("tasks")["name"] == "pk_tasks"
    assert {item["name"] for item in inspector.get_foreign_keys("tasks")} == {
        "fk_tasks_user_id_users",
        "fk_tasks_project_id_user_id_projects",
    }
    assert {item["name"] for item in inspector.get_check_constraints("tasks")} == {
        "ck_tasks_title_not_blank",
        "ck_tasks_status",
        "ck_tasks_priority",
        "ck_tasks_estimated_minutes",
        "ck_tasks_due_at_not_before_planned_date",
        "ck_tasks_completed_at_matches_status",
    }

    base: dict[str, object] = {
        "user_id": owner_id,
        "project_id": project_id,
        "title": "Controlled",
        "status": "TODO",
        "priority": "MEDIUM",
    }
    cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"id": existing_id}, "pk_tasks"),
        ({"user_id": uuid4(), "project_id": uuid4()}, "fk_tasks_user_id_users"),
        (
            {"user_id": other_id, "project_id": project_id},
            "fk_tasks_project_id_user_id_projects",
        ),
        ({"title": "   "}, "ck_tasks_title_not_blank"),
        ({"status": "INVALID"}, "ck_tasks_status"),
        ({"priority": "INVALID"}, "ck_tasks_priority"),
        ({"estimated_minutes": 0}, "ck_tasks_estimated_minutes"),
        (
            {
                "planned_date": "2026-09-02",
                "due_at": datetime(2026, 9, 1, tzinfo=UTC),
            },
            "ck_tasks_due_at_not_before_planned_date",
        ),
        (
            {"status": "COMPLETED", "completed_at": None},
            "ck_tasks_completed_at_matches_status",
        ),
    )
    for overrides, constraint in cases:
        values = base | {"id": uuid4()} | overrides
        with task_harness.session_factory() as session:
            with pytest.raises(IntegrityError) as exc_info:
                session.execute(insert(Task).values(**values))
                session.commit()
            session.rollback()
        diagnostic = getattr(exc_info.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == constraint

    assert task_harness.count_task(existing_id) == 1
    rejected_cross_owner = task_client.post(
        TASKS_PATH,
        headers=bearer(other_token),
        json={"project_id": str(project_id), "title": "Cross owner"},
    )
    assert rejected_cross_owner.status_code == 404
    assert "fk_tasks_project_id_user_id_projects" not in rejected_cross_owner.text
    assert "IntegrityError" not in rejected_cross_owner.text
    assert task_harness.count_project(other_project) == 1
