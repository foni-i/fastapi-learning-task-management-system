"""Ownership-safe Task create use case and transaction boundary."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    ProjectNotFoundError,
    TaskNotFoundError,
)
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskQueryCriteria, TaskRepository
from app.schemas.task import PublicTask, TaskCreate, TaskListQuery, TaskListResponse

TaskRepositoryFactory = Callable[[Session], TaskRepository]
ProjectRepositoryFactory = Callable[[Session], ProjectRepository]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the aware UTC clock used by overdue Task queries."""

    return datetime.now(UTC)


def _query_time(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Task query time must include timezone information")
    return value.astimezone(UTC)


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


def get_owned_task(
    task_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: TaskRepositoryFactory = TaskRepository,
) -> PublicTask:
    """Return one owner-scoped Task without changing the transaction."""

    task = repository_factory(session).get_owned_by_id(
        task_id=task_id,
        user_id=user_id,
    )
    if task is None:
        raise TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)
    return PublicTask.model_validate(task)


def list_owned_tasks(
    query: TaskListQuery,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: TaskRepositoryFactory = TaskRepository,
    clock: Clock = utc_now,
) -> TaskListResponse:
    """Return one deterministic owner-scoped page without a write transaction."""

    criteria = TaskQueryCriteria(
        user_id=user_id,
        project_id=query.project_id,
        status=None if query.status is None else query.status.value,
        priority=None if query.priority is None else query.priority.value,
        planned_from=query.planned_from,
        planned_to=query.planned_to,
        due_from=query.due_from,
        due_to=query.due_to,
        overdue=query.overdue,
        title=query.title,
        now=_query_time(clock),
    )
    repository = repository_factory(session)
    tasks = repository.list_owned(
        criteria=criteria,
        page=query.page,
        page_size=query.page_size,
        sort_by=query.sort_by.value,
        sort_direction=query.sort_direction.value,
    )
    total = repository.count_owned(criteria=criteria)
    pages = 0 if total == 0 else (total + query.page_size - 1) // query.page_size
    return TaskListResponse(
        items=[PublicTask.model_validate(task) for task in tasks],
        page=query.page,
        page_size=query.page_size,
        total=total,
        pages=pages,
    )
