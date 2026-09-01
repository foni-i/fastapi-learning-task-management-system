"""Owned Task persistence and deterministic query operations."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models.task import Task

NON_OVERDUE_STATUSES = ("COMPLETED", "CANCELLED")


@dataclass(frozen=True)
class TaskQueryCriteria:
    """Carry only validated owner-scoped filter values into persistence."""

    user_id: UUID
    project_id: UUID | None
    status: str | None
    priority: str | None
    planned_from: date | None
    planned_to: date | None
    due_from: datetime | None
    due_to: datetime | None
    overdue: bool | None
    title: str | None
    now: datetime


def _escape_like(value: str) -> str:
    """Treat user percent and underscore characters as literal search text."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sort_column(name: str) -> InstrumentedAttribute[object]:
    columns: dict[str, InstrumentedAttribute[object]] = {
        "created_at": Task.created_at,
        "updated_at": Task.updated_at,
        "due_at": Task.due_at,
        "planned_date": Task.planned_date,
        "title": Task.title,
    }
    try:
        return columns[name]
    except KeyError as error:
        raise ValueError("Unsupported Task sort field") from error


def _owned_filters(criteria: TaskQueryCriteria) -> tuple[ColumnElement[bool], ...]:
    predicates: list[ColumnElement[bool]] = [Task.user_id == criteria.user_id]
    if criteria.project_id is not None:
        predicates.append(Task.project_id == criteria.project_id)
    if criteria.status is not None:
        predicates.append(Task.status == criteria.status)
    if criteria.priority is not None:
        predicates.append(Task.priority == criteria.priority)
    if criteria.planned_from is not None:
        predicates.append(Task.planned_date >= criteria.planned_from)
    if criteria.planned_to is not None:
        predicates.append(Task.planned_date <= criteria.planned_to)
    if criteria.due_from is not None:
        predicates.append(Task.due_at >= criteria.due_from)
    if criteria.due_to is not None:
        predicates.append(Task.due_at <= criteria.due_to)
    if criteria.overdue is True:
        predicates.extend(
            (
                Task.due_at < criteria.now,
                Task.status.not_in(NON_OVERDUE_STATUSES),
            )
        )
    elif criteria.overdue is False:
        predicates.append(
            or_(
                Task.due_at.is_(None),
                Task.due_at >= criteria.now,
                Task.status.in_(NON_OVERDUE_STATUSES),
            )
        )
    if criteria.title is not None:
        predicates.append(
            Task.title.ilike(f"%{_escape_like(criteria.title)}%", escape="\\")
        )
    return tuple(predicates)


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        title: str,
        description: str | None,
        planned_date: date | None,
        due_at: datetime | None,
        estimated_minutes: int | None,
        priority: str,
    ) -> Task:
        task = Task(
            user_id=user_id,
            project_id=project_id,
            title=title,
            description=description,
            planned_date=planned_date,
            due_at=due_at,
            estimated_minutes=estimated_minutes,
            priority=priority,
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get_owned_by_id(self, *, task_id: UUID, user_id: UUID) -> Task | None:
        statement = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        return self._session.scalar(statement)

    def list_owned(
        self,
        *,
        criteria: TaskQueryCriteria,
        page: int,
        page_size: int,
        sort_by: str,
        sort_direction: str,
    ) -> list[Task]:
        """Return one stable owner-scoped page from validated query values."""

        sort_column = _sort_column(sort_by)
        if sort_direction == "asc":
            primary_order = sort_column.asc()
            identity_order = Task.id.asc()
        elif sort_direction == "desc":
            primary_order = sort_column.desc()
            identity_order = Task.id.desc()
        else:
            raise ValueError("Unsupported Task sort direction")
        if sort_by in {"due_at", "planned_date"}:
            primary_order = primary_order.nulls_last()

        statement = (
            select(Task)
            .where(*_owned_filters(criteria))
            .order_by(primary_order, identity_order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self._session.scalars(statement))

    def count_owned(self, *, criteria: TaskQueryCriteria) -> int:
        """Count with exactly the same owner and filter predicates as listing."""

        statement = (
            select(func.count()).select_from(Task).where(*_owned_filters(criteria))
        )
        return int(self._session.scalar(statement) or 0)
