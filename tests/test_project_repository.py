"""Unit tests for owner-scoped Project persistence statements."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import Project, ProjectStatus
from app.repositories.projects import ProjectRepository


def sql_text(statement: object) -> str:
    """Compile a SQLAlchemy statement for stable predicate assertions."""

    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )


def test_create_derives_owner_and_only_adds_and_flushes() -> None:
    """Persist trusted ownership without transaction control."""

    session = MagicMock(spec=Session)
    repository = ProjectRepository(session)
    owner_id = uuid4()

    project = repository.create(
        user_id=owner_id,
        name="Plan",
        description=None,
        start_date=None,
        target_date=None,
    )

    assert project.user_id == owner_id
    assert project.name == "Plan"
    session.add.assert_called_once_with(project)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_get_owned_by_id_uses_both_resource_and_owner_predicates() -> None:
    """Never fetch a user-owned resource by public ID alone."""

    session = MagicMock(spec=Session)
    repository = ProjectRepository(session)
    repository.get_owned_by_id(project_id=uuid4(), user_id=uuid4())

    statement = session.scalar.call_args.args[0]
    sql = sql_text(statement)
    assert "projects.id =" in sql
    assert "projects.user_id =" in sql


def test_list_owned_uses_owner_archive_order_and_pagination_contract() -> None:
    """Build fixed filtering, ordering, offset, and limit in one query."""

    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    repository = ProjectRepository(session)

    assert (
        repository.list_owned(
            user_id=uuid4(), page=3, page_size=20, include_archived=False
        )
        == []
    )

    statement = session.scalars.call_args.args[0]
    sql = sql_text(statement)
    assert "projects.user_id =" in sql
    assert "projects.status !=" in sql
    assert "ORDER BY projects.created_at DESC, projects.id DESC" in sql
    assert statement._offset_clause.value == 40
    assert statement._limit_clause.value == 20


def test_list_include_archived_and_count_share_owner_scope() -> None:
    """Include all statuses only when explicitly requested while retaining owner."""

    session = MagicMock(spec=Session)
    session.scalars.return_value = []
    session.scalar.return_value = 7
    repository = ProjectRepository(session)
    owner_id = uuid4()

    repository.list_owned(user_id=owner_id, page=1, page_size=20, include_archived=True)
    assert repository.count_owned(user_id=owner_id, include_archived=True) == 7

    list_sql = sql_text(session.scalars.call_args.args[0])
    count_sql = sql_text(session.scalar.call_args.args[0])
    assert "projects.user_id =" in list_sql
    assert "projects.user_id =" in count_sql
    assert "projects.status !=" not in list_sql
    assert "projects.status !=" not in count_sql


def test_update_changes_only_service_approved_values_and_flushes() -> None:
    """Keep mutation mechanics in persistence and transaction policy outside."""

    session = MagicMock(spec=Session)
    repository = ProjectRepository(session)
    project = Project(
        id=uuid4(),
        user_id=uuid4(),
        name="Old",
        description=None,
        start_date=date(2026, 9, 1),
        target_date=None,
        status=ProjectStatus.NOT_STARTED.value,
        created_at=datetime(2026, 8, 31, tzinfo=UTC),
        updated_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    changed_at = datetime(2026, 9, 1, tzinfo=UTC)

    result = repository.update(
        project,
        values={"name": "New", "status": ProjectStatus.IN_PROGRESS.value},
        updated_at=changed_at,
    )

    assert result is project
    assert project.name == "New"
    assert project.status == ProjectStatus.IN_PROGRESS.value
    assert project.updated_at == changed_at
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
