"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import router as root_router
from app.core.config import Settings, get_settings


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
    application.include_router(root_router)

    return application


app = create_app()
