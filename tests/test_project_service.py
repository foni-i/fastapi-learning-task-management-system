"""Unit tests for ownership-safe Project services and transactions."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ARCHIVED_PROJECT_MESSAGE,
    PROJECT_NOT_FOUND_MESSAGE,
    ArchivedProjectError,
    ProjectNotFoundError,
)
from app.models import Project, ProjectStatus
from app.repositories.projects import ProjectRepository
from app.schemas.project import (
    PROJECT_DATE_ORDER_ERROR_MESSAGE,
    ProjectCreate,
    ProjectUpdate,
)
from app.services.projects import (
    archive_owned_project,
    create_project,
    get_owned_project,
    list_owned_projects,
    update_owned_project,
)

CREATED_AT = datetime(2026, 8, 31, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 8, 31, 2, tzinfo=UTC)


def make_project(
    *,
    owner_id: UUID | None = None,
    status: ProjectStatus = ProjectStatus.NOT_STARTED,
    name: str = "Plan",
) -> Project:
    """Build a fully populated Project suitable for public serialization."""

    return Project(
        id=uuid4(),
        user_id=owner_id or uuid4(),
        name=name,
        description=None,
        start_date=date(2026, 9, 1),
        target_date=date(2026, 9, 30),
        status=status.value,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


class ControlledRepository:
    """Record Service interaction without introducing HTTP or a database."""

    def __init__(
        self,
        project: Project | None = None,
        *,
        projects: list[Project] | None = None,
        total: int = 0,
        failure: Exception | None = None,
    ) -> None:
        self.project = project
        self.projects = projects or []
        self.total = total
        self.failure = failure
        self.created_owner: UUID | None = None
        self.lookup: tuple[UUID, UUID] | None = None
        self.list_arguments: tuple[UUID, int, int, bool] | None = None
        self.count_arguments: tuple[UUID, bool] | None = None
        self.updates: list[dict[str, object]] = []

    def create(self, *, user_id: UUID, **values: object) -> Project:
        self.created_owner = user_id
        if self.failure:
            raise self.failure
        project = make_project(owner_id=user_id, name=cast(str, values["name"]))
        project.description = cast(str | None, values["description"])
        project.start_date = cast(date | None, values["start_date"])
        project.target_date = cast(date | None, values["target_date"])
        self.project = project
        return project

    def get_owned_by_id(self, *, project_id: UUID, user_id: UUID) -> Project | None:
        self.lookup = (project_id, user_id)
        return self.project

    def list_owned(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> list[Project]:
        self.list_arguments = (user_id, page, page_size, include_archived)
        return self.projects

    def count_owned(self, *, user_id: UUID, include_archived: bool) -> int:
        self.count_arguments = (user_id, include_archived)
        return self.total

    def update(
        self,
        project: Project,
        *,
        values: dict[str, object],
        updated_at: datetime,
    ) -> Project:
        if self.failure:
            raise self.failure
        self.updates.append(values)
        for name, value in values.items():
            setattr(project, name, value)
        project.updated_at = updated_at
        return project


def factory(repository: ControlledRepository) -> Callable[[Session], ProjectRepository]:
    """Return a typed factory that also asserts the caller-owned Session."""

    def build(_session: Session) -> ProjectRepository:
        return cast(ProjectRepository, repository)

    return build


def test_create_derives_owner_commits_and_returns_public_allowlist() -> None:
    """Never accept ownership from request data or expose it in output."""

    owner_id = uuid4()
    session = MagicMock(spec=Session)
    repository = ControlledRepository()

    result = create_project(
        ProjectCreate(name="Plan"),
        owner_id,
        session,
        repository_factory=factory(repository),
    )

    assert repository.created_owner == owner_id
    assert set(result.model_dump()) == {
        "id",
        "name",
        "description",
        "start_date",
        "target_date",
        "status",
        "created_at",
        "updated_at",
    }
    assert "user_id" not in result.model_dump_json()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_create_failure_rolls_back_and_propagates() -> None:
    """Restore the transaction without hiding persistence failures."""

    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled failure")
    repository = ControlledRepository(failure=failure)

    with pytest.raises(RuntimeError) as exc_info:
        create_project(
            ProjectCreate(name="Plan"),
            uuid4(),
            session,
            repository_factory=factory(repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_detail_uses_both_ids_and_missing_is_transaction_neutral() -> None:
    """Use the same safe absence for missing and foreign-owned repository results."""

    owner_id = uuid4()
    project_id = uuid4()
    session = MagicMock(spec=Session)
    repository = ControlledRepository()

    with pytest.raises(ProjectNotFoundError) as exc_info:
        get_owned_project(
            project_id,
            owner_id,
            session,
            repository_factory=factory(repository),
        )

    assert str(exc_info.value) == PROJECT_NOT_FOUND_MESSAGE
    assert repository.lookup == (project_id, owner_id)
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_list_forwards_identical_owner_archive_scope_and_builds_pages() -> None:
    """Coordinate deterministic list/count inputs without a write transaction."""

    owner_id = uuid4()
    session = MagicMock(spec=Session)
    project = make_project(owner_id=owner_id)
    repository = ControlledRepository(projects=[project], total=21)

    result = list_owned_projects(
        owner_id,
        session,
        page=2,
        page_size=20,
        include_archived=True,
        repository_factory=factory(repository),
    )

    assert repository.list_arguments == (owner_id, 2, 20, True)
    assert repository.count_arguments == (owner_id, True)
    assert result.total == 21
    assert result.pages == 2
    assert len(result.items) == 1
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_update_combines_persisted_dates_before_any_write() -> None:
    """Reject a partial date that conflicts with the stored counterpart."""

    session = MagicMock(spec=Session)
    project = make_project()
    repository = ControlledRepository(project)

    with pytest.raises(ValueError) as exc_info:
        update_owned_project(
            project.id,
            ProjectUpdate(target_date=date(2026, 8, 31)),
            project.user_id,
            session,
            repository_factory=factory(repository),
        )

    assert str(exc_info.value) == PROJECT_DATE_ORDER_ERROR_MESSAGE
    assert repository.updates == []
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_noop_update_does_not_write_commit_or_change_timestamp() -> None:
    """Return the persisted public value for an effective no-op."""

    session = MagicMock(spec=Session)
    project = make_project()
    repository = ControlledRepository(project)

    result = update_owned_project(
        project.id,
        ProjectUpdate(name="Plan"),
        project.user_id,
        session,
        repository_factory=factory(repository),
        clock=lambda: CHANGED_AT,
    )

    assert result.updated_at == CREATED_AT
    assert repository.updates == []
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_actual_update_commits_and_advances_utc_timestamp() -> None:
    """Commit one Service-approved mutation with an injectable UTC clock."""

    session = MagicMock(spec=Session)
    project = make_project()
    repository = ControlledRepository(project)

    result = update_owned_project(
        project.id,
        ProjectUpdate(name="New", status=ProjectStatus.IN_PROGRESS),
        project.user_id,
        session,
        repository_factory=factory(repository),
        clock=lambda: CHANGED_AT,
    )

    assert repository.updates == [
        {"name": "New", "status": ProjectStatus.IN_PROGRESS.value}
    ]
    assert result.name == "New"
    assert result.status is ProjectStatus.IN_PROGRESS
    assert result.updated_at == CHANGED_AT
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_archived_project_rejects_ordinary_update_without_transaction() -> None:
    """Keep archive state changes behind the dedicated action."""

    session = MagicMock(spec=Session)
    project = make_project(status=ProjectStatus.ARCHIVED)
    repository = ControlledRepository(project)

    with pytest.raises(ArchivedProjectError) as exc_info:
        update_owned_project(
            project.id,
            ProjectUpdate(name="New"),
            project.user_id,
            session,
            repository_factory=factory(repository),
        )

    assert str(exc_info.value) == ARCHIVED_PROJECT_MESSAGE
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_archive_commits_once_and_repeat_is_idempotent() -> None:
    """Write the first archive and leave repeated action transaction-neutral."""

    session = MagicMock(spec=Session)
    project = make_project()
    repository = ControlledRepository(project)

    first = archive_owned_project(
        project.id,
        project.user_id,
        session,
        repository_factory=factory(repository),
        clock=lambda: CHANGED_AT,
    )
    second = archive_owned_project(
        project.id,
        project.user_id,
        session,
        repository_factory=factory(repository),
        clock=lambda: pytest.fail("repeat archive must not read the clock"),
    )

    assert first.status is ProjectStatus.ARCHIVED
    assert second.status is ProjectStatus.ARCHIVED
    assert repository.updates == [{"status": ProjectStatus.ARCHIVED.value}]
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_mutation_failure_rolls_back_and_propagates() -> None:
    """Keep unexpected write failures visible after restoring the transaction."""

    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled failure")
    project = make_project()
    repository = ControlledRepository(project, failure=failure)

    with pytest.raises(RuntimeError) as exc_info:
        archive_owned_project(
            project.id,
            project.user_id,
            session,
            repository_factory=factory(repository),
            clock=lambda: CHANGED_AT,
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
