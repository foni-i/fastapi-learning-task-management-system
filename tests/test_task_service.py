"""Unit tests for Task creation ownership and transaction orchestration."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    TASK_TRANSITION_MESSAGE,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.models import Project, Task, TaskPriority, TaskStatus
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskRepository
from app.schemas.task import (
    SortDirection,
    TaskCreate,
    TaskListQuery,
    TaskSortField,
    TaskUpdate,
)
from app.services.tasks import (
    complete_owned_task,
    create_task,
    delete_owned_task,
    get_owned_task,
    list_owned_tasks,
    update_owned_task,
)


def populated_task(
    user_id: UUID,
    project_id: UUID,
    title: str = "Task",
    *,
    status: TaskStatus = TaskStatus.TODO,
    planned_date: date | None = None,
    due_at: datetime | None = None,
) -> Task:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    completed_at = now if status is TaskStatus.COMPLETED else None
    return Task(
        id=uuid4(),
        user_id=user_id,
        project_id=project_id,
        title=title,
        description=None,
        status=status.value,
        priority="MEDIUM",
        planned_date=planned_date,
        due_at=due_at,
        estimated_minutes=None,
        completed_at=completed_at,
        created_at=now,
        updated_at=now,
    )


def test_create_checks_owned_project_commits_and_hides_owner() -> None:
    session = MagicMock(spec=Session)
    user_id, project_id = uuid4(), uuid4()
    project_repo = MagicMock(spec=ProjectRepository)
    project_repo.get_owned_by_id.return_value = Project(id=project_id, user_id=user_id)
    task_repo = MagicMock(spec=TaskRepository)
    task_repo.create.return_value = populated_task(user_id, project_id)

    result = create_task(
        TaskCreate(project_id=project_id, title="  Task  "),
        user_id,
        session,
        project_repository_factory=lambda received: cast(
            ProjectRepository, project_repo
        ),
        task_repository_factory=lambda received: cast(TaskRepository, task_repo),
    )

    project_repo.get_owned_by_id.assert_called_once_with(
        project_id=project_id, user_id=user_id
    )
    assert task_repo.create.call_args.kwargs["user_id"] == user_id
    assert task_repo.create.call_args.kwargs["title"] == "Task"
    assert task_repo.create.call_args.kwargs["priority"] == "MEDIUM"
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    assert "user_id" not in result.model_dump()


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_missing_and_foreign_project_share_prewrite_neutral_error(kind: str) -> None:
    session = MagicMock(spec=Session)
    project_repo = MagicMock(spec=ProjectRepository)
    project_repo.get_owned_by_id.return_value = None
    task_repo = MagicMock(spec=TaskRepository)
    with pytest.raises(ProjectNotFoundError) as exc_info:
        create_task(
            TaskCreate(project_id=uuid4(), title="Task"),
            uuid4(),
            session,
            project_repository_factory=lambda received: cast(
                ProjectRepository, project_repo
            ),
            task_repository_factory=lambda received: cast(TaskRepository, task_repo),
        )
    assert kind in {"missing", "foreign"}
    assert str(exc_info.value) == PROJECT_NOT_FOUND_MESSAGE
    task_repo.create.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_create_failure_rolls_back_and_propagates() -> None:
    session = MagicMock(spec=Session)
    user_id, project_id = uuid4(), uuid4()
    project_repo = MagicMock(spec=ProjectRepository)
    project_repo.get_owned_by_id.return_value = Project(id=project_id, user_id=user_id)
    failure = RuntimeError("controlled failure")
    task_repo = MagicMock(spec=TaskRepository)
    task_repo.create.side_effect = failure
    with pytest.raises(RuntimeError) as exc_info:
        create_task(
            TaskCreate(project_id=project_id, title="Task"),
            user_id,
            session,
            project_repository_factory=lambda received: cast(
                ProjectRepository, project_repo
            ),
            task_repository_factory=lambda received: cast(TaskRepository, task_repo),
        )
    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_detail_uses_both_ids_and_missing_is_transaction_neutral() -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    task_id, user_id = uuid4(), uuid4()
    repository.get_owned_by_id.return_value = None

    with pytest.raises(TaskNotFoundError) as exc_info:
        get_owned_task(
            task_id,
            user_id,
            session,
            repository_factory=lambda received: cast(TaskRepository, repository),
        )

    assert str(exc_info.value) == TASK_NOT_FOUND_MESSAGE
    repository.get_owned_by_id.assert_called_once_with(task_id=task_id, user_id=user_id)
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_list_builds_shared_criteria_page_metadata_and_public_items() -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    user_id, project_id = uuid4(), uuid4()
    repository.list_owned.return_value = [populated_task(user_id, project_id)]
    repository.count_owned.return_value = 21
    now = datetime(2026, 9, 2, 8, tzinfo=UTC)
    query = TaskListQuery(
        page=2,
        page_size=20,
        project_id=project_id,
        status=TaskStatus.TODO,
        priority=TaskPriority.HIGH,
        overdue=True,
        title="Study",
        sort_by=TaskSortField.DUE_AT,
        sort_direction=SortDirection.ASC,
    )

    result = list_owned_tasks(
        query,
        user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: now,
    )

    list_call = repository.list_owned.call_args.kwargs
    criteria = list_call["criteria"]
    assert criteria.user_id == user_id
    assert criteria.project_id == project_id
    assert criteria.status == "TODO"
    assert criteria.priority == "HIGH"
    assert criteria.overdue is True
    assert criteria.now == now
    assert repository.count_owned.call_args.kwargs["criteria"] is criteria
    assert (list_call["page"], list_call["page_size"]) == (2, 20)
    assert (list_call["sort_by"], list_call["sort_direction"]) == ("due_at", "asc")
    assert (result.total, result.pages, len(result.items)) == (21, 2, 1)
    assert "user_id" not in result.model_dump_json()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_empty_list_has_zero_pages_and_requires_aware_clock() -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    repository.list_owned.return_value = []
    repository.count_owned.return_value = 0

    def factory(_session: Session) -> TaskRepository:
        return cast(TaskRepository, repository)

    result = list_owned_tasks(
        TaskListQuery(),
        uuid4(),
        session,
        repository_factory=factory,
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert (result.items, result.total, result.pages) == ([], 0, 0)

    with pytest.raises(ValueError):
        list_owned_tasks(
            TaskListQuery(),
            uuid4(),
            session,
            repository_factory=factory,
            clock=lambda: datetime(2026, 9, 1),
        )


def mutating_repository(task: Task) -> MagicMock:
    repository = MagicMock(spec=TaskRepository)
    repository.get_owned_by_id.return_value = task

    def update(
        received: Task, *, values: dict[str, object], updated_at: datetime
    ) -> Task:
        for name, value in values.items():
            setattr(received, name, value)
        received.updated_at = updated_at
        return received

    repository.update.side_effect = update
    return repository


def test_update_combines_fields_commits_once_and_returns_public_allowlist() -> None:
    session = MagicMock(spec=Session)
    user_id, project_id = uuid4(), uuid4()
    task = populated_task(
        user_id,
        project_id,
        planned_date=date(2026, 9, 2),
        due_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    repository = mutating_repository(task)
    changed_at = datetime(2026, 9, 2, 9, tzinfo=timezone(timedelta(hours=8)))

    result = update_owned_task(
        task.id,
        TaskUpdate(
            title="  New  ",
            description="  Detail  ",
            due_at=datetime(2026, 9, 2, tzinfo=UTC),
            estimated_minutes=60,
            priority=TaskPriority.HIGH,
            status=TaskStatus.IN_PROGRESS,
        ),
        user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: changed_at,
    )

    call = repository.update.call_args.kwargs
    assert call["values"] == {
        "title": "New",
        "description": "Detail",
        "due_at": datetime(2026, 9, 2, tzinfo=UTC),
        "estimated_minutes": 60,
        "priority": "HIGH",
        "status": "IN_PROGRESS",
    }
    assert call["updated_at"] == datetime(2026, 9, 2, 1, tzinfo=UTC)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    assert "user_id" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("planned_date", "due_at", "update"),
    [
        (
            date(2026, 9, 2),
            None,
            TaskUpdate(due_at=datetime(2026, 9, 1, 23, 59, tzinfo=UTC)),
        ),
        (
            None,
            datetime(2026, 9, 1, 23, 59, tzinfo=UTC),
            TaskUpdate(planned_date=date(2026, 9, 2)),
        ),
    ],
)
def test_update_rejects_effective_date_conflict_before_write_or_clock(
    planned_date: date | None, due_at: datetime | None, update: TaskUpdate
) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), planned_date=planned_date, due_at=due_at)
    repository = mutating_repository(task)

    with pytest.raises(TaskDateOrderError):
        update_owned_task(
            task.id,
            update,
            task.user_id,
            session,
            repository_factory=lambda received: cast(TaskRepository, repository),
            clock=lambda: pytest.fail("invalid dates must not read the clock"),
        )
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_update_explicitly_clears_nullable_fields() -> None:
    session = MagicMock(spec=Session)
    task = populated_task(
        uuid4(),
        uuid4(),
        planned_date=date(2026, 9, 2),
        due_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    task.description = "Detail"
    task.estimated_minutes = 30
    repository = mutating_repository(task)
    update_owned_task(
        task.id,
        TaskUpdate(
            description=None,
            planned_date=None,
            due_at=None,
            estimated_minutes=None,
        ),
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert repository.update.call_args.kwargs["values"] == {
        "description": None,
        "planned_date": None,
        "due_at": None,
        "estimated_minutes": None,
    }


def test_update_noop_is_transaction_and_clock_neutral() -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4())
    repository = mutating_repository(task)
    result = update_owned_task(
        task.id,
        TaskUpdate(title="Task", status=TaskStatus.TODO),
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: pytest.fail("no-op must not read the clock"),
    )
    assert result.updated_at == task.updated_at
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.TODO, TaskStatus.IN_PROGRESS),
        (TaskStatus.TODO, TaskStatus.CANCELLED),
        (TaskStatus.IN_PROGRESS, TaskStatus.TODO),
        (TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED),
        (TaskStatus.CANCELLED, TaskStatus.TODO),
    ],
)
def test_update_accepts_each_non_completed_transition(
    current: TaskStatus, target: TaskStatus
) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), status=current)
    repository = mutating_repository(task)
    result = update_owned_task(
        task.id,
        TaskUpdate(status=target),
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert result.status is target
    session.commit.assert_called_once_with()


def test_update_rejects_cancelled_to_in_progress_before_write() -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), status=TaskStatus.CANCELLED)
    repository = mutating_repository(task)
    with pytest.raises(TaskTransitionError) as exc_info:
        update_owned_task(
            task.id,
            TaskUpdate(status=TaskStatus.IN_PROGRESS),
            task.user_id,
            session,
            repository_factory=lambda received: cast(TaskRepository, repository),
            clock=lambda: pytest.fail("invalid transition must not read the clock"),
        )
    assert str(exc_info.value) == TASK_TRANSITION_MESSAGE
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "target", [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED]
)
def test_update_reopens_completed_and_atomically_clears_timestamp(
    target: TaskStatus,
) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), status=TaskStatus.COMPLETED)
    repository = mutating_repository(task)
    result = update_owned_task(
        task.id,
        TaskUpdate(status=target),
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert repository.update.call_args.kwargs["values"] == {
        "completed_at": None,
        "status": target.value,
    }
    assert result.status is target
    assert result.completed_at is None
    session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "initial", [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED]
)
def test_complete_sets_one_server_timestamp_and_commits(initial: TaskStatus) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), status=initial)
    repository = mutating_repository(task)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return datetime(2026, 9, 2, 9, tzinfo=timezone(timedelta(hours=8)))

    result = complete_owned_task(
        task.id,
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=clock,
    )
    timestamp = datetime(2026, 9, 2, 1, tzinfo=UTC)
    assert calls == 1
    assert repository.update.call_args.args == (task,)
    assert repository.update.call_args.kwargs == {
        "values": {"status": "COMPLETED", "completed_at": timestamp},
        "updated_at": timestamp,
    }
    assert result.status is TaskStatus.COMPLETED
    assert result.completed_at == result.updated_at == timestamp
    session.commit.assert_called_once_with()


def test_repeated_completion_is_transaction_and_clock_neutral() -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4(), status=TaskStatus.COMPLETED)
    repository = mutating_repository(task)
    original = task.completed_at
    original_updated_at = task.updated_at
    result = complete_owned_task(
        task.id,
        task.user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
        clock=lambda: pytest.fail("repeat completion must not read the clock"),
    )
    assert result.completed_at == original
    assert result.updated_at == original_updated_at
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "complete"])
def test_lifecycle_rejects_naive_clock_before_write(operation: str) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4())
    repository = mutating_repository(task)
    with pytest.raises(ValueError):
        if operation == "update":
            update_owned_task(
                task.id,
                TaskUpdate(title="New"),
                task.user_id,
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
                clock=lambda: datetime(2026, 9, 2),
            )
        else:
            complete_owned_task(
                task.id,
                task.user_id,
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
                clock=lambda: datetime(2026, 9, 2),
            )
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "complete"])
@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_lifecycle_missing_and_foreign_share_transaction_neutral_error(
    operation: str, kind: str
) -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    repository.get_owned_by_id.return_value = None
    with pytest.raises(TaskNotFoundError) as exc_info:
        if operation == "update":
            update_owned_task(
                uuid4(),
                TaskUpdate(title="New"),
                uuid4(),
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
            )
        else:
            complete_owned_task(
                uuid4(),
                uuid4(),
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
            )
    assert kind in {"missing", "foreign"}
    assert str(exc_info.value) == TASK_NOT_FOUND_MESSAGE
    repository.update.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "complete"])
def test_lifecycle_write_failure_rolls_back_and_propagates(operation: str) -> None:
    session = MagicMock(spec=Session)
    task = populated_task(uuid4(), uuid4())
    repository = mutating_repository(task)
    failure = RuntimeError("controlled lifecycle failure")
    repository.update.side_effect = failure
    with pytest.raises(RuntimeError) as exc_info:
        if operation == "update":
            update_owned_task(
                task.id,
                TaskUpdate(title="New"),
                task.user_id,
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
                clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
            )
        else:
            complete_owned_task(
                task.id,
                task.user_id,
                session,
                repository_factory=lambda received: cast(TaskRepository, repository),
                clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
            )
    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_delete_owned_task_commits_once_without_public_result() -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    repository.delete_owned.return_value = True
    task_id, user_id = uuid4(), uuid4()

    delete_owned_task(
        task_id,
        user_id,
        session,
        repository_factory=lambda received: cast(TaskRepository, repository),
    )

    repository.delete_owned.assert_called_once_with(task_id=task_id, user_id=user_id)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_delete_owned_task_hides_missing_and_foreign_without_transaction(
    kind: str,
) -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    repository.delete_owned.return_value = False

    with pytest.raises(TaskNotFoundError) as exc_info:
        delete_owned_task(
            uuid4(),
            uuid4(),
            session,
            repository_factory=lambda received: cast(TaskRepository, repository),
        )

    assert kind in {"missing", "foreign"}
    assert str(exc_info.value) == TASK_NOT_FOUND_MESSAGE
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("failure_point", ["repository", "commit"])
def test_delete_owned_task_rolls_back_write_failures(failure_point: str) -> None:
    session = MagicMock(spec=Session)
    repository = MagicMock(spec=TaskRepository)
    failure = RuntimeError("controlled delete failure")
    if failure_point == "repository":
        repository.delete_owned.side_effect = failure
    else:
        repository.delete_owned.return_value = True
        session.commit.side_effect = failure

    with pytest.raises(RuntimeError) as exc_info:
        delete_owned_task(
            uuid4(),
            uuid4(),
            session,
            repository_factory=lambda received: cast(TaskRepository, repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
