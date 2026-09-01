"""Tests for the minimal FastAPI application factory and instance."""

from fastapi import FastAPI

from app.core.config import AppEnvironment, Settings
from app.main import app, create_app


def test_default_application_is_importable_with_basic_metadata() -> None:
    """The module-level application is importable with only its platform route."""

    assert isinstance(app, FastAPI)
    assert app.title == "FastAPI STMS"
    assert app.debug is False
    paths = app.openapi()["paths"]

    assert set(paths) == {
        "/health/live",
        "/health/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/users/me",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/archive",
    }
    assert set(paths["/api/v1/auth/login"]) == {"post"}
    assert set(paths["/api/v1/auth/register"]) == {"post"}
    assert set(paths["/api/v1/users/me"]) == {"get", "patch"}
    assert set(paths["/api/v1/projects"]) == {"get", "post"}
    assert set(paths["/api/v1/projects/{project_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/projects/{project_id}/archive"]) == {"post"}


def test_create_app_applies_explicit_settings() -> None:
    """The factory applies metadata, debug, and documentation settings."""

    settings = Settings(
        app_name="Test STMS",
        env=AppEnvironment.TEST,
        debug=True,
        api_docs_enabled=False,
    )

    test_app = create_app(settings)

    assert test_app.title == "Test STMS"
    assert test_app.debug is True
    assert test_app.docs_url is None
    assert test_app.redoc_url is None
    assert test_app.openapi_url is None
