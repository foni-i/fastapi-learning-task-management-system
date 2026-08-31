"""Reusable HTTP dependencies for authenticated request identity."""

from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AUTHENTICATION_REQUIRED_MESSAGE
from app.core.tokens import AccessTokenError, validate_access_token
from app.db.session import get_session
from app.models.user import User
from app.repositories.users import UserRepository

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Short-lived access token issued by POST /api/v1/auth/login",
)


def _raise_authentication_error() -> NoReturn:
    """Return one safe challenge for every invalid authentication path."""

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AUTHENTICATION_REQUIRED_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """Validate one Bearer token and resolve its persisted user subject."""

    if (
        credentials is None
        or credentials.scheme.casefold() != "bearer"
        or not credentials.credentials
    ):
        _raise_authentication_error()

    try:
        claims = validate_access_token(credentials.credentials)
    except AccessTokenError:
        _raise_authentication_error()

    user = UserRepository(session).get_by_id(claims.subject)
    if user is None:
        _raise_authentication_error()
    return user
