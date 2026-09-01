"""Unit tests for Task creation ownership and transaction orchestration."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import PROJECT_NOT_FOUND_MESSAGE, ProjectNotFoundError
from app.models import Project, Task
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskRepository
from app.schemas.task import TaskCreate
from app.services.tasks import create_task


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
