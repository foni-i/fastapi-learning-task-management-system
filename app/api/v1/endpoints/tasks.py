"""Versioned authenticated Task endpoints."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.exceptions import (
    PROJECT_NOT_FOUND_MESSAGE,
    TASK_NOT_FOUND_MESSAGE,
    TASK_TRANSITION_MESSAGE,
    ProjectNotFoundError,
    TaskDateOrderError,
    TaskNotFoundError,
    TaskTransitionError,
)
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import AuthenticationErrorResponse
from app.schemas.project import ProjectErrorResponse
from app.schemas.task import (
    TASK_DATE_ORDER_MESSAGE,
    PublicTask,
    TaskCreate,
    TaskErrorResponse,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.tasks import (
    complete_owned_task,
    create_task,
    delete_owned_task,
    get_owned_task,
    list_owned_tasks,
    update_owned_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

AUTHENTICATION_RESPONSE = {
    "model": AuthenticationErrorResponse,
    "description": "Bearer authentication required",
}
TASK_NOT_FOUND_RESPONSE = {
    "model": TaskErrorResponse,
    "description": "Task absent or not owned by the current user",
}
PROJECT_NOT_FOUND_RESPONSE = {
    "model": ProjectErrorResponse,
    "description": "Project absent or not owned by the current user",
}


def _raise_task_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=TASK_NOT_FOUND_MESSAGE,
    )


@router.post(
    "",
    response_model=PublicTask,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: PROJECT_NOT_FOUND_RESPONSE,
    },
)
def create_task_endpoint(
    task_input: TaskCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicTask:
    """Create one Task below a Project owned by the authenticated user."""

    try:
        return create_task(task_input, current_user.id, session)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=PROJECT_NOT_FOUND_MESSAGE,
        ) from None


@router.get(
    "",
    response_model=TaskListResponse,
    responses={status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE},
)
def list_tasks_endpoint(
    query: Annotated[TaskListQuery, Query()],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TaskListResponse:
    """Return one bounded owner-scoped Task page."""

    return list_owned_tasks(query, current_user.id, session)


@router.get(
    "/{task_id}",
    response_model=PublicTask,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: TASK_NOT_FOUND_RESPONSE,
    },
)
def get_task_endpoint(
    task_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicTask:
    """Return one Task without revealing foreign-owned records."""

    try:
        return get_owned_task(task_id, current_user.id, session)
    except TaskNotFoundError:
        _raise_task_not_found()


@router.patch(
    "/{task_id}",
    response_model=PublicTask,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: TASK_NOT_FOUND_RESPONSE,
    },
)
def update_task_endpoint(
    task_id: UUID,
    task_update: TaskUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicTask:
    """Apply one strict owner-scoped Task update."""

    try:
        return update_owned_task(
            task_id,
            task_update,
            current_user.id,
            session,
        )
    except TaskNotFoundError:
        _raise_task_not_found()
    except TaskDateOrderError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=TASK_DATE_ORDER_MESSAGE,
        ) from None
    except TaskTransitionError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=TASK_TRANSITION_MESSAGE,
        ) from None


@router.post(
    "/{task_id}/complete",
    response_model=PublicTask,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: TASK_NOT_FOUND_RESPONSE,
    },
)
def complete_task_endpoint(
    task_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicTask:
    """Complete one owned Task through the server-owned lifecycle action."""

    try:
        return complete_owned_task(task_id, current_user.id, session)
    except TaskNotFoundError:
        _raise_task_not_found()


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: TASK_NOT_FOUND_RESPONSE,
    },
)
def delete_task_endpoint(
    task_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Permanently delete one Task without revealing foreign ownership."""

    try:
        delete_owned_task(task_id, current_user.id, session)
    except TaskNotFoundError:
        _raise_task_not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
