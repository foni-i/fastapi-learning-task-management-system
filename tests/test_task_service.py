"""Unit tests for Task creation ownership and transaction orchestration."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    ProjectNotFoundError,
    TaskNotFoundError,
)
from app.models import Project, Task, TaskPriority, TaskStatus
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskRepository
from app.schemas.task import (
    SortDirection,
    TaskCreate,
    TaskListQuery,
    TaskSortField,
)
from app.services.tasks import create_task, get_owned_task, list_owned_tasks


def populated_task(user_id: UUID, project_id: UUID, title: str = "Task") -> Task:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    return Task(
        id=uuid4(),
        user_id=user_id,
        project_id=project_id,
        title=title,
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
