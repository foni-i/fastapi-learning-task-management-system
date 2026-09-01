"""Versioned authenticated Project endpoints."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.exceptions import (
    ARCHIVED_PROJECT_MESSAGE,
    PROJECT_NOT_FOUND_MESSAGE,
    ArchivedProjectError,
    ProjectDateOrderError,
    ProjectNotFoundError,
)
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import AuthenticationErrorResponse
from app.schemas.project import (
    PROJECT_DATE_ORDER_ERROR_MESSAGE,
    ProjectCreate,
    ProjectErrorResponse,
    ProjectListResponse,
    ProjectUpdate,
    PublicProject,
)
from app.services.projects import (
    archive_owned_project,
    create_project,
    get_owned_project,
    list_owned_projects,
    update_owned_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])

AUTHENTICATION_RESPONSE = {
    "model": AuthenticationErrorResponse,
    "description": "Bearer authentication required",
}
NOT_FOUND_RESPONSE = {
    "model": ProjectErrorResponse,
    "description": "Project absent or not owned by the current user",
}


def _raise_project_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=PROJECT_NOT_FOUND_MESSAGE,
    )


@router.post(
    "",
    response_model=PublicProject,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE},
)
def create_project_endpoint(
    project_input: ProjectCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicProject:
    """Create one Project owned by the authenticated user."""

    return create_project(project_input, current_user.id, session)


@router.get(
    "",
    response_model=ProjectListResponse,
    responses={status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE},
)
def list_projects_endpoint(
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = False,
) -> ProjectListResponse:
    """Return one deterministic page of the authenticated user's Projects."""

    return list_owned_projects(
        current_user.id,
        session,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
    )


@router.get(
    "/{project_id}",
    response_model=PublicProject,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
    },
)
def get_project_endpoint(
    project_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicProject:
    """Return one owner-scoped Project without revealing foreign records."""

    try:
        return get_owned_project(project_id, current_user.id, session)
    except ProjectNotFoundError:
        _raise_project_not_found()


@router.patch(
    "/{project_id}",
    response_model=PublicProject,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: {
            "model": ProjectErrorResponse,
            "description": "Archived Project cannot be modified",
        },
    },
)
def update_project_endpoint(
    project_id: UUID,
    project_update: ProjectUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicProject:
    """Apply a strict partial update through the Project service."""

    try:
        return update_owned_project(
            project_id,
            project_update,
            current_user.id,
            session,
        )
    except ProjectNotFoundError:
        _raise_project_not_found()
    except ArchivedProjectError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ARCHIVED_PROJECT_MESSAGE,
        ) from None
    except ProjectDateOrderError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=PROJECT_DATE_ORDER_ERROR_MESSAGE,
        ) from None


@router.post(
    "/{project_id}/archive",
    response_model=PublicProject,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
    },
)
def archive_project_endpoint(
    project_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicProject:
    """Archive one owned Project through its idempotent service action."""

    try:
        return archive_owned_project(project_id, current_user.id, session)
    except ProjectNotFoundError:
        _raise_project_not_found()
