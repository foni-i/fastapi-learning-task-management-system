"""Connection-free tests for strict Stage 7 Task schemas."""

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import TaskPriority, TaskStatus
from app.schemas.task import (
    PublicTask,
    SortDirection,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskSortField,
    TaskUpdate,
)


def test_create_normalizes_and_exposes_exact_fields() -> None:
    value = TaskCreate(
        project_id=uuid4(),
        title="  Learn  ",
        description="   ",
        planned_date=date(2026, 9, 2),
        due_at=datetime(2026, 9, 2, 8, tzinfo=timezone(timedelta(hours=8))),
        estimated_minutes=1,
    )
    assert value.title == "Learn"
    assert value.description is None
    assert value.due_at == datetime(2026, 9, 2, 0, tzinfo=UTC)
    assert value.priority is TaskPriority.MEDIUM
    assert set(value.model_dump()) == {
        "project_id",
        "title",
        "description",
        "planned_date",
        "due_at",
        "estimated_minutes",
        "priority",
    }


@pytest.mark.parametrize("estimate", [0, 1441])
def test_create_rejects_estimate_bounds(estimate: int) -> None:
    with pytest.raises(ValidationError):
        TaskCreate(project_id=uuid4(), title="Valid", estimated_minutes=estimate)


@pytest.mark.parametrize("estimate", [1, 1440])
def test_create_accepts_estimate_bounds(estimate: int) -> None:
    assert (
        TaskCreate(
            project_id=uuid4(), title="Valid", estimated_minutes=estimate
        ).estimated_minutes
        == estimate
    )


@pytest.mark.parametrize(
    "extra", ["id", "user_id", "status", "completed_at", "created_at", "updated_at"]
)
def test_create_rejects_internal_fields(extra: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(
            {"project_id": str(uuid4()), "title": "Valid", extra: "x"}
        )


def test_create_rejects_naive_and_early_due_time() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(project_id=uuid4(), title="Valid", due_at=datetime(2026, 9, 2))
    with pytest.raises(ValidationError):
        TaskCreate(
            project_id=uuid4(),
            title="Valid",
            planned_date=date(2026, 9, 2),
            due_at=datetime(2026, 9, 1, 23, 59, tzinfo=UTC),
        )


def test_update_preserves_missing_and_explicit_null() -> None:
    update = TaskUpdate(
        description=None, planned_date=None, due_at=None, estimated_minutes=None
    )
    assert update.model_fields_set == {
        "description",
        "planned_date",
        "due_at",
        "estimated_minutes",
    }
    assert update.model_dump(exclude_unset=True) == {
        "description": None,
        "planned_date": None,
        "due_at": None,
        "estimated_minutes": None,
    }
    for payload in (
        {},
        {"title": None},
        {"priority": None},
        {"status": None},
        {"status": "COMPLETED"},
    ):
        with pytest.raises(ValidationError):
            TaskUpdate.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["id", "user_id", "project_id", "completed_at", "created_at", "updated_at"],
)
def test_update_rejects_internal_and_server_owned_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        TaskUpdate.model_validate({field: "forbidden"})


def test_query_defaults_bounds_ranges_and_sort_allowlist() -> None:
    query = TaskListQuery()
    assert (query.page, query.page_size) == (1, 20)
    assert query.sort_by is TaskSortField.CREATED_AT
    assert query.sort_direction is SortDirection.DESC
    for payload in (
        {"page": 0},
        {"page_size": 101},
        {"sort_by": "user_id"},
        {"sort_direction": "sideways"},
        {"planned_from": "2026-09-02", "planned_to": "2026-09-01"},
        {"due_from": "2026-09-02T00:00:00Z", "due_to": "2026-09-01T00:00:00Z"},
        {"title": "   "},
    ):
        with pytest.raises(ValidationError):
            TaskListQuery.model_validate(payload)


def test_public_task_is_strict_utc_allowlist_without_owner() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    source = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        title="Task",
        description=None,
        status="TODO",
        priority="MEDIUM",
        planned_date=None,
        due_at=None,
        estimated_minutes=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    public = PublicTask.model_validate(source)
    assert set(public.model_dump()) == {
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
    assert "user_id" not in public.model_dump_json()
    page = TaskListResponse(items=[public], page=1, page_size=20, total=1, pages=1)
    assert page.total == 1


def test_public_rejects_naive_or_inconsistent_completion() -> None:
    base = dict(
        id=uuid4(),
        project_id=uuid4(),
        title="Task",
        description=None,
        priority=TaskPriority.MEDIUM,
        planned_date=None,
        due_at=None,
        estimated_minutes=None,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        PublicTask.model_validate(
            {
                **base,
                "status": TaskStatus.TODO,
                "completed_at": datetime(2026, 9, 1),
            }
        )
    with pytest.raises(ValidationError):
        PublicTask.model_validate(
            {**base, "status": TaskStatus.COMPLETED, "completed_at": None}
        )
