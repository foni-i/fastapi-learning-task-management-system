"""Versioned endpoints for the authenticated user's public account view."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_current_user
from app.models.user import User
from app.schemas.auth import AuthenticationErrorResponse
from app.schemas.user import PublicUser

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
