"""Versioned authentication endpoints introduced by Stage 3."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE, DuplicateEmailError
from app.db.session import get_session
from app.schemas.user import (
    PublicUser,
    RegistrationConflictResponse,
    UserRegistrationRequest,
)
from app.services.registration import register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=PublicUser,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "model": RegistrationConflictResponse,
            "description": "Canonical email already registered",
        }
    },
)
def register_user_endpoint(
    registration: UserRegistrationRequest,
    session: Annotated[Session, Depends(get_session)],
) -> PublicUser:
    """Delegate one validated registration request to the application service."""

    try:
        return register_user(registration, session)
    except DuplicateEmailError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_EMAIL_MESSAGE,
        ) from None
