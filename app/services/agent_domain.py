"""Narrow synchronous runtime gateway from Agent tools to Domain Services."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.projects import list_owned_projects
from app.services.tasks import create_task, list_owned_tasks, update_owned_task

SessionFactory = Callable[[], Session]


type ListProjectsService = Callable[..., ProjectListResponse]
type ListTasksService = Callable[..., TaskListResponse]
type CreateTaskService = Callable[..., PublicTask]
type UpdateTaskService = Callable[..., PublicTask]


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
    ) -> None:
        self._session_factory = session_factory
        self._list_projects_service = list_projects_service
        self._list_tasks_service = list_tasks_service
        self._create_task_service = create_task_service
        self._update_task_service = update_task_service

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
