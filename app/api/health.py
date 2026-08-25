"""Unversioned process and dependency health check endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.db.probe import check_database_connection
from app.db.session import DatabaseConfigurationError
from app.schemas.health import (
    LivenessResponse,
    ReadinessResponse,
    ReadinessUnavailableResponse,
)

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "/live",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness check",
    description="Confirms that the API process is running without checking dependencies.",
)
def get_liveness() -> LivenessResponse:
    """Report that the application process can handle requests."""

    return LivenessResponse(status="ok")


def database_is_ready() -> bool:
    """Translate expected configuration/database failures into readiness state."""

    try:
        check_database_connection()
    except DatabaseConfigurationError, SQLAlchemyError:
        return False
    return True


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessUnavailableResponse,
            "description": "PostgreSQL is unavailable or not configured.",
        }
    },
    summary="Readiness check",
    description="Confirms that PostgreSQL can answer a minimal query.",
)
def get_readiness(
    ready: Annotated[bool, Depends(database_is_ready)],
) -> ReadinessResponse | JSONResponse:
    """Report dependency readiness without exposing database failure details."""

    if not ready:
        unavailable = ReadinessUnavailableResponse(status="unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=unavailable.model_dump(),
        )
    return ReadinessResponse(status="ok")
