"""Owned Project persistence operations with caller-owned transactions."""

from collections.abc import Mapping
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectStatus


class ProjectRepository:
    """Persist and query Projects through one supplied synchronous Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: UUID,
        name: str,
        description: str | None,
        start_date: date | None,
        target_date: date | None,
    ) -> Project:
        """Add one owner-derived Project and flush without committing."""

        project = Project(
            user_id=user_id,
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
        )
        self._session.add(project)
        self._session.flush()
        return project

    def get_owned_by_id(self, *, project_id: UUID, user_id: UUID) -> Project | None:
        """Return a Project only when both resource and owner IDs match."""

        statement = select(Project).where(
            Project.id == project_id,
            Project.user_id == user_id,
        )
        return self._session.scalar(statement)

    def list_owned(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> list[Project]:
        """Return one deterministic owner-scoped page."""

        statement = select(Project).where(Project.user_id == user_id)
        if not include_archived:
            statement = statement.where(Project.status != ProjectStatus.ARCHIVED.value)
        statement = (
            statement.order_by(Project.created_at.desc(), Project.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self._session.scalars(statement))

    def count_owned(self, *, user_id: UUID, include_archived: bool) -> int:
        """Count using the same ownership and archive predicates as listing."""

        statement = (
            select(func.count()).select_from(Project).where(Project.user_id == user_id)
        )
        if not include_archived:
            statement = statement.where(Project.status != ProjectStatus.ARCHIVED.value)
        return int(self._session.scalar(statement) or 0)

    def update(
        self,
        project: Project,
        *,
        values: Mapping[str, object],
        updated_at: datetime,
    ) -> Project:
        """Apply a Service-approved mutation and flush without committing."""

        for field_name, value in values.items():
            setattr(project, field_name, value)
        project.updated_at = updated_at
        self._session.flush()
        return project
