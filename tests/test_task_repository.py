"""Unit tests for the owner-scoped synchronous Task Repository."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import Task
from app.repositories.tasks import TaskQueryCriteria, TaskRepository


def sql_text(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )


def criteria(**overrides: object) -> TaskQueryCriteria:
    values: dict[str, object] = {
        "user_id": uuid4(),
        "project_id": None,
        "status": None,
        "priority": None,
        "planned_from": None,
        "planned_to": None,
        "due_from": None,
        "due_to": None,
        "overdue": None,
        "title": None,
        "now": datetime(2026, 9, 1, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return TaskQueryCriteria(**values)  # type: ignore[arg-type]


def test_owned_lookup_contains_task_and_owner_predicates() -> None:
    session = MagicMock(spec=Session)
    repository = TaskRepository(session)
    task_id, user_id = uuid4(), uuid4()
    repository.get_owned_by_id(task_id=task_id, user_id=user_id)
    statement = session.scalar.call_args.args[0]
    sql = str(statement)
    assert "tasks.id =" in sql
    assert "tasks.user_id =" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_create_adds_and_flushes_only_canonical_values() -> None:
    session = MagicMock(spec=Session)
    repository = TaskRepository(session)
    user_id, project_id = uuid4(), uuid4()
    task = repository.create(
        user_id=user_id,
        project_id=project_id,
        title="Task",
        description=None,
        planned_date=None,
        due_at=None,
        estimated_minutes=30,
        priority="HIGH",
    )
    assert isinstance(task, Task)
    assert (task.user_id, task.project_id, task.title, task.priority) == (
        user_id,
        project_id,
        "Task",
        "HIGH",
    )
    session.add.assert_called_once_with(task)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_list_and_count_share_all_owner_filter_predicates() -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    session.scalar.return_value = 0
    repository = TaskRepository(session)
    filters = criteria(
        project_id=uuid4(),
        status="TODO",
        priority="HIGH",
        planned_from=date(2026, 9, 1),
        planned_to=date(2026, 9, 30),
        due_from=datetime(2026, 9, 1, tzinfo=UTC),
        due_to=datetime(2026, 10, 1, tzinfo=UTC),
        overdue=True,
        title="100%_focus",
    )

    repository.list_owned(
        criteria=filters,
        page=2,
        page_size=10,
        sort_by="created_at",
        sort_direction="desc",
    )
    repository.count_owned(criteria=filters)

    list_statement = session.scalars.call_args.args[0]
    count_statement = session.scalar.call_args.args[0]
    assert str(list_statement.whereclause) == str(count_statement.whereclause)
    sql = sql_text(list_statement)
    for fragment in (
        "tasks.user_id =",
        "tasks.project_id =",
        "tasks.status =",
        "tasks.priority =",
        "tasks.planned_date >=",
        "tasks.planned_date <=",
        "tasks.due_at >=",
        "tasks.due_at <=",
        "tasks.due_at <",
        "tasks.status NOT IN",
        "tasks.title ILIKE",
    ):
        assert fragment in sql
    assert list_statement._offset_clause.value == 10
    assert list_statement._limit_clause.value == 10
    assert "%100\\%\\_focus%" in list_statement.compile().params.values()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"project_id": uuid4()}, "tasks.project_id ="),
        ({"status": "IN_PROGRESS"}, "tasks.status ="),
        ({"priority": "URGENT"}, "tasks.priority ="),
        ({"planned_from": date(2026, 9, 1)}, "tasks.planned_date >="),
        ({"planned_to": date(2026, 9, 30)}, "tasks.planned_date <="),
        ({"due_from": datetime(2026, 9, 1, tzinfo=UTC)}, "tasks.due_at >="),
        ({"due_to": datetime(2026, 9, 30, tzinfo=UTC)}, "tasks.due_at <="),
        ({"title": "focus"}, "tasks.title ILIKE"),
    ],
)
def test_each_optional_filter_preserves_owner_scope(
    override: dict[str, object], fragment: str
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    repository = TaskRepository(session)
    repository.list_owned(
        criteria=criteria(**override),
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_direction="desc",
    )
    sql = sql_text(session.scalars.call_args.args[0])
    assert "tasks.user_id =" in sql
    assert fragment in sql


@pytest.mark.parametrize(
    ("overdue", "fragments"),
    [
        (True, ("tasks.due_at <", "tasks.status NOT IN")),
        (
            False,
            ("tasks.due_at IS NULL", "tasks.due_at >=", "tasks.status IN"),
        ),
    ],
)
def test_overdue_filter_has_exact_status_and_time_boundaries(
    overdue: bool, fragments: tuple[str, ...]
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    repository = TaskRepository(session)
    repository.list_owned(
        criteria=criteria(overdue=overdue),
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_direction="desc",
    )
    sql = sql_text(session.scalars.call_args.args[0])
    for fragment in fragments:
        assert fragment in sql


@pytest.mark.parametrize(
    "sort_by", ["created_at", "updated_at", "due_at", "planned_date", "title"]
)
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sort_allowlist_is_stable_and_places_nullable_values_last(
    sort_by: str, direction: str
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    repository = TaskRepository(session)
    repository.list_owned(
        criteria=criteria(),
        page=1,
        page_size=20,
        sort_by=sort_by,
        sort_direction=direction,
    )
    sql = sql_text(session.scalars.call_args.args[0])
    order = direction.upper()
    assert f"tasks.{sort_by} {order}" in sql
    assert f"tasks.id {order}" in sql
    assert ("NULLS LAST" in sql) is (sort_by in {"due_at", "planned_date"})


@pytest.mark.parametrize(
    ("sort_by", "direction"), [("user_id", "asc"), ("title", "sideways")]
)
def test_repository_rejects_non_allowlisted_sort_input(
    sort_by: str, direction: str
) -> None:
    session = MagicMock(spec=Session)
    repository = TaskRepository(session)
    with pytest.raises(ValueError):
        repository.list_owned(
            criteria=criteria(),
            page=1,
            page_size=20,
            sort_by=sort_by,
            sort_direction=direction,
        )
    session.scalars.assert_not_called()
