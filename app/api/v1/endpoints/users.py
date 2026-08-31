"""Versioned endpoints for the authenticated user's public account view."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import AuthenticationErrorResponse
from app.schemas.user import (
    CurrentUserEmailUpdate,
    PublicUser,
    RegistrationConflictResponse,
)
from app.services.current_user import update_current_user_email

router = APIRouter(prefix="/users", tags=["users"])


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
