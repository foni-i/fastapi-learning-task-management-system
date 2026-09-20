"""Versioned authentication endpoints introduced by Stage 3."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AUTHENTICATION_UNAVAILABLE_MESSAGE,
    DUPLICATE_EMAIL_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.refresh_tokens import INVALID_REFRESH_TOKEN_MESSAGE
from app.db.session import get_session
from app.schemas.auth import (
    AuthenticationErrorResponse,
    AuthenticationValidationResponse,
    RefreshTokenRequest,
    TokenPairResponse,
    UserLoginRequest,
)
from app.schemas.user import (
    PublicUser,
    RegistrationConflictResponse,
    UserRegistrationRequest,
)
from app.services.authentication import authenticate_user, refresh_authentication
from app.services.refresh_tokens import logout_refresh_token
from app.services.registration import register_user

AUTH_CACHE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
AUTH_CREDENTIAL_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/users/me/change-password",
    }
)


class CredentialRoute(APIRoute):
    """Bound auth failures, including dependency setup, without leaking diagnostics."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if request.url.path not in AUTH_CREDENTIAL_PATHS:
                return await original(request)
            try:
                response = await original(request)
            except RequestValidationError:
                # The app handler owns safe 422 serialization and cache headers.
                raise
            except StarletteHTTPException as error:
                if error.status_code == 400:
                    # Invalid bytes/encoding can fail before JSON schema validation.
                    return JSONResponse(
                        status_code=422,
                        content={
                            "detail": [
                                {
                                    "loc": ["body"],
                                    "type": "invalid_request",
                                    "msg": "Invalid authentication request",
                                }
                            ]
                        },
                        headers=AUTH_CACHE_HEADERS,
                    )
                error.headers = {**(error.headers or {}), **AUTH_CACHE_HEADERS}
                raise
            except Exception:
                return JSONResponse(
                    status_code=503,
                    content={"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE},
                    headers=AUTH_CACHE_HEADERS,
                )
            response.headers.update(AUTH_CACHE_HEADERS)
            return response

        return handler


router = APIRouter(prefix="/auth", tags=["auth"], route_class=CredentialRoute)


@router.post(
    "/login",
    response_model=TokenPairResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": AuthenticationErrorResponse,
            "description": "Invalid login credentials",
        },
        422: {"model": AuthenticationValidationResponse},
        503: {"model": AuthenticationErrorResponse},
    },
)
def login_user_endpoint(
    credentials: UserLoginRequest,
    session: Annotated[Session, Depends(get_session)],
) -> TokenPairResponse:
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
    "/refresh",
    response_model=TokenPairResponse,
    responses={
        401: {"model": AuthenticationErrorResponse},
        422: {"model": AuthenticationValidationResponse},
        503: {"model": AuthenticationErrorResponse},
    },
)
def refresh_token_endpoint(
    credentials: RefreshTokenRequest,
    session: Annotated[Session, Depends(get_session)],
) -> TokenPairResponse:
    """Authenticate with the body credential, independent of any access JWT."""

    try:
        return refresh_authentication(credentials.refresh_token, session)
    except InvalidRefreshTokenError:
        raise HTTPException(
            status_code=401, detail=INVALID_REFRESH_TOKEN_MESSAGE
        ) from None


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    responses={
        422: {"model": AuthenticationValidationResponse},
        503: {"model": AuthenticationErrorResponse},
    },
)
def logout_user_endpoint(
    credentials: RefreshTokenRequest,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Revoke the body credential without requiring a live access JWT."""
    logout_refresh_token(credentials.refresh_token, session)
    return Response(status_code=204)


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
