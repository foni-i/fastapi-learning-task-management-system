"""Health check response schemas."""

from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    """Stable response returned when the application process is alive."""

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """Stable response returned when PostgreSQL answers the readiness probe."""

    status: Literal["ok"]


class ReadinessUnavailableResponse(BaseModel):
    """Safe response returned when PostgreSQL is not ready."""

    status: Literal["unavailable"]
