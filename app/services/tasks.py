"""Ownership-safe Task create use case and transaction boundary."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    TASK_TRANSITION_MESSAGE,
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.models.task import Task, TaskPriority, TaskStatus
from app.repositories.projects import ProjectRepository
from app.repositories.tasks import TaskQueryCriteria, TaskRepository
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
    validate_task_date_order,
)

TaskRepositoryFactory = Callable[[Session], TaskRepository]
ProjectRepositoryFactory = Callable[[Session], ProjectRepository]
Clock = Callable[[], datetime]

ALLOWED_NON_COMPLETED_TRANSITIONS = {
    (TaskStatus.TODO, TaskStatus.IN_PROGRESS),
    (TaskStatus.TODO, TaskStatus.CANCELLED),
    (TaskStatus.IN_PROGRESS, TaskStatus.TODO),
    (TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED),
    (TaskStatus.CANCELLED, TaskStatus.TODO),
}


def utc_now() -> datetime:
    """Return the aware UTC clock used by overdue Task queries."""

    return datetime.now(UTC)


def _query_time(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Task query time must include timezone information")
    return value.astimezone(UTC)


def _require_owned_task(task: Task | None) -> Task:
    if task is None:
        raise TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)
    return task


def _validate_status_transition(current: str, target: TaskStatus) -> None:
    current_status = TaskStatus(current)
    if current_status is target:
        return
    if current_status is TaskStatus.COMPLETED:
        return
    if (current_status, target) not in ALLOWED_NON_COMPLETED_TRANSITIONS:
        raise TaskTransitionError(TASK_TRANSITION_MESSAGE)


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


def create_task_batch(
    task_inputs: tuple[TaskCreate, ...],
    user_id: UUID,
    session: Session,
    *,
    task_repository_factory: TaskRepositoryFactory = TaskRepository,
    project_repository_factory: ProjectRepositoryFactory = ProjectRepository,
) -> tuple[PublicTask, ...]:
    """Create one bounded same-Project batch in a single Service transaction."""

    if not 1 <= len(task_inputs) <= 10:
        raise ValueError("Task batch must contain between one and ten items")
    project_id = task_inputs[0].project_id
    if any(item.project_id != project_id for item in task_inputs):
        raise ValueError("Task batch must target one project")
    project = project_repository_factory(session).get_owned_by_id(
        project_id=project_id,
        user_id=user_id,
    )
    if project is None:
        raise ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)

    repository = task_repository_factory(session)
    try:
        created: list[PublicTask] = []
        for task_input in task_inputs:
            values = task_input.model_dump()
            values["priority"] = task_input.priority.value
            task = repository.create(user_id=user_id, **values)
            created.append(PublicTask.model_validate(task))
        session.commit()
        return tuple(created)
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

    task = _require_owned_task(
        repository_factory(session).get_owned_by_id(
            task_id=task_id,
            user_id=user_id,
        )
    )
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


def update_owned_task(
    task_id: UUID,
    task_update: TaskUpdate,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: TaskRepositoryFactory = TaskRepository,
    clock: Clock = utc_now,
) -> PublicTask:
    """Apply an ownership-safe partial update and own its write transaction."""

    repository = repository_factory(session)
    task = _require_owned_task(
        repository.get_owned_by_id(task_id=task_id, user_id=user_id)
    )
    supplied = task_update.model_dump(exclude_unset=True)
    planned_date = supplied.get("planned_date", task.planned_date)
    due_at = supplied.get("due_at", task.due_at)
    validate_task_date_order(planned_date, due_at)

    changes: dict[str, object] = {}
    for field_name, value in supplied.items():
        stored_value = (
            value.value if isinstance(value, (TaskPriority, TaskStatus)) else value
        )
        if field_name == "status":
            assert isinstance(value, TaskStatus)
            _validate_status_transition(task.status, value)
            if task.status == TaskStatus.COMPLETED.value and value.value != task.status:
                changes["completed_at"] = None
        if getattr(task, field_name) != stored_value:
            changes[field_name] = stored_value
    if not changes:
        return PublicTask.model_validate(task)

    changed_at = _query_time(clock)
    try:
        updated = repository.update(task, values=changes, updated_at=changed_at)
        result = PublicTask.model_validate(updated)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def complete_owned_task(
    task_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: TaskRepositoryFactory = TaskRepository,
    clock: Clock = utc_now,
) -> PublicTask:
    """Complete one owned Task once with a server-owned timestamp."""

    repository = repository_factory(session)
    task = _require_owned_task(
        repository.get_owned_by_id(task_id=task_id, user_id=user_id)
    )
    if task.status == TaskStatus.COMPLETED.value:
        return PublicTask.model_validate(task)

    completed_at = _query_time(clock)
    try:
        updated = repository.update(
            task,
            values={
                "status": TaskStatus.COMPLETED.value,
                "completed_at": completed_at,
            },
            updated_at=completed_at,
        )
        result = PublicTask.model_validate(updated)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def delete_owned_task(
    task_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: TaskRepositoryFactory = TaskRepository,
) -> None:
    """Permanently delete one owned Task within a Service-owned transaction."""

    repository = repository_factory(session)
    try:
        deleted = repository.delete_owned(task_id=task_id, user_id=user_id)
        if not deleted:
            raise TaskNotFoundError(TASK_NOT_FOUND_MESSAGE)
        session.commit()
    except TaskNotFoundError:
        raise
    except Exception:
        session.rollback()
        raise
