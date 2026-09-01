"""Owned Task persistence operations with caller-owned transactions."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task


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
