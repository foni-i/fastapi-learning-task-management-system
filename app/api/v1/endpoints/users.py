"""Versioned endpoints for the authenticated user's public account view."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints.auth import CredentialRoute
from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    DUPLICATE_EMAIL_MESSAGE,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import (
    AuthenticationErrorResponse,
    AuthenticationValidationResponse,
)
from app.schemas.user import (
    CurrentUserEmailUpdate,
    PasswordChangeRequest,
    PublicUser,
    RegistrationConflictResponse,
)
from app.services.current_user import update_current_user_email
from app.services.password_change import change_password

router = APIRouter(prefix="/users", tags=["users"], route_class=CredentialRoute)


@router.post(
    "/me/change-password",
    status_code=204,
    response_class=Response,
    responses={
        401: {"model": AuthenticationErrorResponse},
        422: {"model": AuthenticationValidationResponse},
        503: {"model": AuthenticationErrorResponse},
    },
)
def change_password_endpoint(
    credentials: PasswordChangeRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        change_password(credentials, current_user.id, session)
    except InvalidCredentialsError:
        raise HTTPException(
            401, AUTHENTICATION_REQUIRED_MESSAGE, headers={"WWW-Authenticate": "Bearer"}
        ) from None
    return Response(status_code=204)


@router.get(
    "/me",
    response_model=PublicUser,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": AuthenticationErrorResponse,
            "description": "Bearer authentication required",
        }
    },
)
def get_current_user_endpoint(
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicUser:
    """Return the existing public allowlist for the authenticated user."""

    return PublicUser.model_validate(current_user)


@router.patch(
    "/me",
    response_model=PublicUser,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": AuthenticationErrorResponse,
            "description": "Bearer authentication required",
        },
        status.HTTP_409_CONFLICT: {
            "model": RegistrationConflictResponse,
            "description": "Canonical email already registered",
        },
    },
)
def update_current_user_email_endpoint(
    update: CurrentUserEmailUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicUser:
    """Delegate the authenticated user's email-only update to its service."""

    try:
        return update_current_user_email(update, current_user, session)
    except DuplicateEmailError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_EMAIL_MESSAGE,
        ) from None
