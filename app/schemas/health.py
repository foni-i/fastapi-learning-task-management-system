"""Health check response schemas."""

from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    """Stable response returned when the application process is alive."""

    status: Literal["ok"]
