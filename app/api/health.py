"""Process-level health check endpoints."""

from fastapi import APIRouter, status

from app.schemas.health import LivenessResponse

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
