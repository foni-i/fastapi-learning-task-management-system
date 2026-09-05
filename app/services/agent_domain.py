"""Narrow synchronous runtime gateway from Agent tools to Domain Services."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.schemas.agent_tool import AgentToolMutationResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.projects import list_owned_projects
from app.services.tasks import (
    create_task,
    create_task_batch,
    delete_owned_task,
    list_owned_tasks,
    update_owned_task,
)

SessionFactory = Callable[[], Session]


type ListProjectsService = Callable[..., ProjectListResponse]
type ListTasksService = Callable[..., TaskListResponse]
type CreateTaskService = Callable[..., PublicTask]
type UpdateTaskService = Callable[..., PublicTask]
type BatchCreateTasksService = Callable[..., tuple[PublicTask, ...]]
type DeleteTaskService = Callable[..., None]


def _new_session() -> Session:
    """Resolve the configured factory lazily when a valid tool is executed."""

    return get_session_factory()()


class AgentDomainGateway:
    """Own one Session lifecycle while preserving existing Service contracts."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = _new_session,
        list_projects_service: ListProjectsService = list_owned_projects,
        list_tasks_service: ListTasksService = list_owned_tasks,
        create_task_service: CreateTaskService = create_task,
        update_task_service: UpdateTaskService = update_owned_task,
        batch_create_tasks_service: BatchCreateTasksService = create_task_batch,
        delete_task_service: DeleteTaskService = delete_owned_task,
    ) -> None:
        self._session_factory = session_factory
        self._list_projects_service = list_projects_service
        self._list_tasks_service = list_tasks_service
        self._create_task_service = create_task_service
        self._update_task_service = update_task_service
        self._batch_create_tasks_service = batch_create_tasks_service
        self._delete_task_service = delete_task_service

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        """List owned Projects without adding a write transaction."""

        session = self._session_factory()
        try:
            return self._list_projects_service(
                user_id,
                session,
                page=page,
                page_size=page_size,
                include_archived=include_archived,
            )
        finally:
            session.close()

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        """List owned Tasks without adding a write transaction."""

        session = self._session_factory()
        try:
            return self._list_tasks_service(query, user_id, session)
        finally:
            session.close()

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        """Create one owned Task while leaving the transaction to its Service."""

        session = self._session_factory()
        try:
            return self._create_task_service(task_input, user_id, session)
        finally:
            session.close()

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        """Update one owned Task while leaving the transaction to its Service."""

        session = self._session_factory()
        try:
            return self._update_task_service(
                task_id,
                task_update,
                user_id,
                session,
            )
        finally:
            session.close()

    def batch_create_tasks(
        self,
        *,
        user_id: UUID,
        task_inputs: tuple[TaskCreate, ...],
    ) -> AgentToolMutationResult:
        """Delegate one atomic bounded batch to the existing Domain boundary."""

        session = self._session_factory()
        try:
            created = self._batch_create_tasks_service(task_inputs, user_id, session)
            return AgentToolMutationResult(
                operation="batch_create_tasks",
                reference_task_id=created[0].id,
                affected_count=len(created),
                summary=f"Created {len(created)} tasks",
            )
        finally:
            session.close()

    def delete_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
    ) -> AgentToolMutationResult:
        """Delegate one owner-scoped deletion and return a bounded receipt."""

        session = self._session_factory()
        try:
            self._delete_task_service(task_id, user_id, session)
            return AgentToolMutationResult(
                operation="delete_task",
                reference_task_id=task_id,
                affected_count=1,
                summary="Task deleted",
            )
        finally:
            session.close()
