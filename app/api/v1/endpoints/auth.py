"""Versioned authentication endpoints introduced by Stage 3."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DUPLICATE_EMAIL_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from app.db.session import get_session
from app.schemas.auth import (
    AccessTokenResponse,
    AuthenticationErrorResponse,
    UserLoginRequest,
)
from app.schemas.user import (
    PublicUser,
    RegistrationConflictResponse,
    UserRegistrationRequest,
)
from app.services.authentication import authenticate_user
from app.services.registration import register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": AuthenticationErrorResponse,
            "description": "Invalid login credentials",
        }
    },
)
def login_user_endpoint(
    credentials: UserLoginRequest,
    session: Annotated[Session, Depends(get_session)],
) -> AccessTokenResponse:
    """Delegate validated credentials to the authentication service."""

    try:
        return authenticate_user(credentials, session)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


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
