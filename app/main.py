"""FastAPI application entry point."""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import router as root_router
from app.core.config import Settings, get_settings
from app.middleware.request_body_limit import (
    MULTIPART_ENVELOPE_BYTES,
    UploadBodyLimitMiddleware,
)
from app.services.knowledge_documents import MAX_DOCUMENT_BYTES

MASKED_SECRET = "**********"


def _safe_validation_errors(
    exception: RequestValidationError,
) -> list[dict[str, object]]:
    """Preserve FastAPI's error shape while masking password inputs."""

    safe_errors: list[dict[str, object]] = []
    for error in exception.errors():
        safe_error: dict[str, object] = dict(error)
        location = safe_error.get("loc")
        input_value = safe_error.get("input")
        if isinstance(location, tuple) and "password" in location:
            safe_error["input"] = MASKED_SECRET
        elif isinstance(input_value, dict) and "password" in input_value:
            safe_input = dict(input_value)
            safe_input["password"] = MASKED_SECRET
            safe_error["input"] = safe_input
        safe_errors.append(safe_error)
    return safe_errors


async def _request_validation_exception_handler(
    _request: Request,
    exception: Exception,
) -> JSONResponse:
    """Return the standard 422 detail without reflecting submitted passwords."""

    assert isinstance(exception, RequestValidationError)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder({"detail": _safe_validation_errors(exception)}),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance from explicit or environment settings."""

    current_settings = settings if settings is not None else get_settings()
    docs_url = "/docs" if current_settings.api_docs_enabled else None
    redoc_url = "/redoc" if current_settings.api_docs_enabled else None
    openapi_url = "/openapi.json" if current_settings.api_docs_enabled else None

    application = FastAPI(
        title=current_settings.app_name,
        debug=current_settings.debug,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    application.add_exception_handler(
        RequestValidationError,
        _request_validation_exception_handler,
    )
    application.add_middleware(
        UploadBodyLimitMiddleware,
        max_body_bytes=MAX_DOCUMENT_BYTES + MULTIPART_ENVELOPE_BYTES,
    )
    application.include_router(root_router)

    return application


app = create_app()
