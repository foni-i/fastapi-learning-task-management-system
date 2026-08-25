"""Tests for the process liveness endpoint and its API contract."""

from fastapi.testclient import TestClient

from app.main import app


def test_liveness_endpoint_returns_stable_response_from_application() -> None:
    """The real application serves the stable liveness response with HTTP 200."""

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_endpoint_rejects_unsupported_method() -> None:
    """The liveness contract only supports GET requests."""

    with TestClient(app) as client:
        response = client.post("/health/live")

    assert response.status_code == 405


def test_unknown_versioned_path_returns_not_found() -> None:
    """The mounted v1 boundary exposes no product endpoint yet."""

    with TestClient(app) as client:
        response = client.get("/api/v1/not-yet-implemented")

    assert response.status_code == 404


def test_liveness_endpoint_is_documented_in_openapi() -> None:
    """OpenAPI exposes the operation and its explicit response schema."""

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    operation = openapi["paths"]["/health/live"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]

    assert operation["summary"] == "Liveness check"
    assert operation["tags"] == ["health"]
    assert response_schema == {"$ref": "#/components/schemas/LivenessResponse"}
    assert openapi["components"]["schemas"]["LivenessResponse"]["required"] == [
        "status"
    ]
