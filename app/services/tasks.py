"""Ownership-safe Task create use case and transaction boundary."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import PROJECT_NOT_FOUND_MESSAGE, ProjectNotFoundError
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskRepository
from app.schemas.task import PublicTask, TaskCreate

TaskRepositoryFactory = Callable[[Session], TaskRepository]
ProjectRepositoryFactory = Callable[[Session], ProjectRepository]


def create_task(
    task_input: TaskCreate,
    user_id: UUID,
    session: Session,
    *,
    task_repository_factory: TaskRepositoryFactory = TaskRepository,
    project_repository_factory: ProjectRepositoryFactory = ProjectRepository,
) -> PublicTask:
    """Create one Task only below a Project owned by the trusted user."""

    project_repository = project_repository_factory(session)
    project = project_repository.get_owned_by_id(
        project_id=task_input.project_id,
        user_id=user_id,
    )
    if project is None:
        raise ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)

    task_repository = task_repository_factory(session)
    values = task_input.model_dump()
    values["priority"] = task_input.priority.value
    try:
        task = task_repository.create(user_id=user_id, **values)
        result = PublicTask.model_validate(task)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
