"""Ownership-safe Project use cases and synchronous transaction boundaries."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    ARCHIVED_PROJECT_MESSAGE,
    PROJECT_NOT_FOUND_MESSAGE,
    ArchivedProjectError,
    ProjectNotFoundError,
)
from app.models.project import Project, ProjectStatus
from app.repositories.projects import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectUpdate,
    PublicProject,
    validate_project_date_order,
)

RepositoryFactory = Callable[[Session], ProjectRepository]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return an aware UTC timestamp for a real Project mutation."""

    return datetime.now(UTC)


def _mutation_time(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Project mutation time must include timezone information")
    return value.astimezone(UTC)


def _require_owned_project(project: Project | None) -> Project:
    if project is None:
        raise ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)
    return project


def create_project(
    project_input: ProjectCreate,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = ProjectRepository,
) -> PublicProject:
    """Create one trusted-owner Project and commit the complete use case."""

    try:
        repository = repository_factory(session)
        project = repository.create(user_id=user_id, **project_input.model_dump())
        result = PublicProject.model_validate(project)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def get_owned_project(
    project_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = ProjectRepository,
) -> PublicProject:
    """Read one owner-scoped Project without changing the transaction."""

    repository = repository_factory(session)
    project = _require_owned_project(
        repository.get_owned_by_id(project_id=project_id, user_id=user_id)
    )
    return PublicProject.model_validate(project)


def list_owned_projects(
    user_id: UUID,
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    include_archived: bool = False,
    repository_factory: RepositoryFactory = ProjectRepository,
) -> ProjectListResponse:
    """Read one deterministic owner-scoped page without committing."""

    repository = repository_factory(session)
    projects = repository.list_owned(
        user_id=user_id,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
    )
    total = repository.count_owned(
        user_id=user_id,
        include_archived=include_archived,
    )
    pages = 0 if total == 0 else (total + page_size - 1) // page_size
    return ProjectListResponse(
        items=[PublicProject.model_validate(project) for project in projects],
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


def update_owned_project(
    project_id: UUID,
    project_update: ProjectUpdate,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = ProjectRepository,
    clock: Clock = utc_now,
) -> PublicProject:
    """Apply one strict partial update and own its write transaction."""

    repository = repository_factory(session)
    project = _require_owned_project(
        repository.get_owned_by_id(project_id=project_id, user_id=user_id)
    )
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ArchivedProjectError(ARCHIVED_PROJECT_MESSAGE)

    supplied = project_update.model_dump(exclude_unset=True)
    start_date = supplied.get("start_date", project.start_date)
    target_date = supplied.get("target_date", project.target_date)
    validate_project_date_order(start_date, target_date)

    changes: dict[str, object] = {}
    for field_name, value in supplied.items():
        stored_value = value.value if isinstance(value, ProjectStatus) else value
        if getattr(project, field_name) != stored_value:
            changes[field_name] = stored_value
    if not changes:
        return PublicProject.model_validate(project)

    try:
        updated = repository.update(
            project,
            values=changes,
            updated_at=_mutation_time(clock),
        )
        result = PublicProject.model_validate(updated)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def archive_owned_project(
    project_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = ProjectRepository,
    clock: Clock = utc_now,
) -> PublicProject:
    """Archive once; repeated calls remain read-only and idempotent."""

    repository = repository_factory(session)
    project = _require_owned_project(
        repository.get_owned_by_id(project_id=project_id, user_id=user_id)
    )
    if project.status == ProjectStatus.ARCHIVED.value:
        return PublicProject.model_validate(project)

    try:
        updated = repository.update(
            project,
            values={"status": ProjectStatus.ARCHIVED.value},
            updated_at=_mutation_time(clock),
        )
        result = PublicProject.model_validate(updated)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
