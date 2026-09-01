"""Unit tests for the owner-scoped synchronous Task Repository."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Task
from app.repositories.tasks import TaskRepository


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
