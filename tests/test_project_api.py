"""Connection-free API tests for authenticated Project endpoints."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import projects
from app.core.exceptions import (
    ARCHIVED_PROJECT_MESSAGE,
    AUTHENTICATION_REQUIRED_MESSAGE,
    PROJECT_NOT_FOUND_MESSAGE,
    ArchivedProjectError,
    ProjectDateOrderError,
    ProjectNotFoundError,
)
from app.db.session import get_session
from app.main import app
from app.models import ProjectStatus, User
from app.schemas.project import (
    PROJECT_DATE_ORDER_ERROR_MESSAGE,
    ProjectCreate,
    ProjectListResponse,
    ProjectUpdate,
    PublicProject,
)

PUBLIC_FIELDS = {
    "id",
    "name",
    "description",
    "start_date",
    "target_date",
    "status",
    "created_at",
    "updated_at",
}


def make_user() -> User:
    """Build a controlled authenticated user without exposing internal data."""

    timestamp = datetime(2026, 9, 1, 8, tzinfo=UTC)
    return cast(
        User,
        SimpleNamespace(
            id=uuid4(),
            email="project-owner@example.com",
            password_hash="internal-test-hash",
            created_at=timestamp,
            updated_at=timestamp,
        ),
    )


def make_public_project(
    *,
    project_id: UUID | None = None,
    name: str = "Study plan",
    description: str | None = None,
    start_date: date | None = date(2026, 9, 1),
    target_date: date | None = date(2026, 9, 30),
    status: ProjectStatus = ProjectStatus.NOT_STARTED,
) -> PublicProject:
    """Build the exact public Project response allowlist."""

    timestamp = datetime(2026, 9, 1, 8, tzinfo=UTC)
    return PublicProject(
        id=project_id or uuid4(),
        name=name,
        description=description,
        start_date=start_date,
        target_date=target_date,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.fixture
def project_request_context() -> Iterator[tuple[MagicMock, User, list[str]]]:
    """Provide one cached request Session and authenticated identity."""

    session = MagicMock(spec=Session)
    user = make_user()
    lifecycle: list[str] = []

    def override_session() -> Iterator[Session]:
        lifecycle.append("opened")
        try:
            yield session
        finally:
            lifecycle.append("closed")

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield session, user, lifecycle
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_create_project_delegates_canonical_input_owner_and_same_session(
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep authenticated creation as one thin Router-to-Service call."""

    session, user, lifecycle = project_request_context
    observed: dict[str, object] = {}

    def fake_create(
        project_input: ProjectCreate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicProject:
        observed.update(
            input=project_input,
            user_id=user_id,
            session=received_session,
        )
        return make_public_project(
            name=project_input.name,
            description=project_input.description,
            start_date=project_input.start_date,
            target_date=project_input.target_date,
        )

    monkeypatch.setattr(projects, "create_project", fake_create)
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "  Agent plan  ",
            "description": "  milestone  ",
            "start_date": "2026-09-01",
            "target_date": "2026-09-30",
        },
    )

    assert response.status_code == 201
    assert set(response.json()) == PUBLIC_FIELDS
    assert response.json()["name"] == "Agent plan"
    assert response.json()["description"] == "milestone"
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    assert isinstance(observed["input"], ProjectCreate)
    assert "user_id" not in response.text
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Valid", "user_id": str(uuid4())},
        {"name": "Valid", "status": "IN_PROGRESS"},
        {"name": "Valid", "unknown": "value"},
        {
            "name": "Valid",
            "start_date": "2026-09-02",
            "target_date": "2026-09-01",
        },
    ],
    ids=("owner", "status", "extra", "date-order"),
)
def test_create_project_rejects_invalid_payload_before_service(
    payload: dict[str, object],
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject internal, extra, and invalid date input with 422."""

    _session, _user, lifecycle = project_request_context
    service = MagicMock()
    monkeypatch.setattr(projects, "create_project", service)

    response = client.post("/api/v1/projects", json=payload)

    assert response.status_code == 422
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


def test_project_routes_require_existing_bearer_challenge(
    client: TestClient,
) -> None:
    """Protect every Project method with the accepted authentication boundary."""

    project_id = uuid4()
    session = MagicMock(spec=Session)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        requests = (
            client.post("/api/v1/projects", json={"name": "Valid"}),
            client.get("/api/v1/projects"),
            client.get(f"/api/v1/projects/{project_id}"),
            client.patch(f"/api/v1/projects/{project_id}", json={"name": "Valid"}),
            client.post(f"/api/v1/projects/{project_id}/archive"),
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    for response in requests:
        assert response.status_code == 401
        assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
        assert response.headers["www-authenticate"] == "Bearer"


def test_project_detail_delegates_both_ids_and_same_session(
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass public and trusted owner IDs only to the owned read Service."""

    session, user, lifecycle = project_request_context
    project_id = uuid4()
    observed: dict[str, object] = {}

    def fake_get(
        received_project_id: UUID,
        user_id: UUID,
        received_session: Session,
    ) -> PublicProject:
        observed.update(
            project_id=received_project_id,
            user_id=user_id,
            session=received_session,
        )
        return make_public_project(project_id=received_project_id)

    monkeypatch.setattr(projects, "get_owned_project", fake_get)
    response = client.get(f"/api/v1/projects/{project_id}")

    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert observed == {
        "project_id": project_id,
        "user_id": user.id,
        "session": session,
    }
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_project_detail_hides_missing_and_foreign_owned_with_same_404(
    kind: str,
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose no resource-enumeration distinction at the HTTP boundary."""

    _session, _user, lifecycle = project_request_context

    def reject(*_args: object) -> PublicProject:
        raise ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)

    monkeypatch.setattr(projects, "get_owned_project", reject)
    response = client.get(f"/api/v1/projects/{uuid4()}")

    assert kind in {"missing", "foreign"}
    assert response.status_code == 404
    assert response.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}
    assert lifecycle == ["opened", "closed"]


def test_project_detail_rejects_malformed_uuid_before_service(
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep path parsing at the Router boundary."""

    _session, _user, lifecycle = project_request_context
    service = MagicMock()
    monkeypatch.setattr(projects, "get_owned_project", service)
    response = client.get("/api/v1/projects/not-a-uuid")

    assert response.status_code == 422
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", (1, 20, False)),
        ("?page=2&page_size=5&include_archived=true", (2, 5, True)),
    ],
    ids=("defaults", "explicit"),
)
def test_project_list_forwards_only_bounded_pagination_contract(
    query: str,
    expected: tuple[int, int, bool],
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward the fixed page and archive inputs to the read Service."""

    session, user, lifecycle = project_request_context
    observed: dict[str, object] = {}

    def fake_list(
        user_id: UUID,
        received_session: Session,
        *,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        observed.update(
            user_id=user_id,
            session=received_session,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return ProjectListResponse(
            items=[make_public_project()],
            page=page,
            page_size=page_size,
            total=1,
            pages=1,
        )

    monkeypatch.setattr(projects, "list_owned_projects", fake_list)
    response = client.get(f"/api/v1/projects{query}")

    assert response.status_code == 200
    assert set(response.json()) == {"items", "page", "page_size", "total", "pages"}
    assert set(response.json()["items"][0]) == PUBLIC_FIELDS
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    assert (
        observed["page"],
        observed["page_size"],
        observed["include_archived"],
    ) == expected
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize("query", ["?page=0", "?page_size=0", "?page_size=101"])
def test_project_list_rejects_invalid_pagination_before_service(
    query: str,
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce pagination bounds at the HTTP input boundary."""

    _session, _user, lifecycle = project_request_context
    service = MagicMock()
    monkeypatch.setattr(projects, "list_owned_projects", service)
    response = client.get(f"/api/v1/projects{query}")

    assert response.status_code == 422
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


def test_project_update_preserves_missing_null_and_owner_context(
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward exact PATCH semantics and trusted owner identity to Service."""

    session, user, lifecycle = project_request_context
    project_id = uuid4()
    observed: dict[str, object] = {}

    def fake_update(
        received_project_id: UUID,
        update: ProjectUpdate,
        user_id: UUID,
        received_session: Session,
    ) -> PublicProject:
        observed.update(
            project_id=received_project_id,
            update=update,
            user_id=user_id,
            session=received_session,
        )
        return make_public_project(
            project_id=received_project_id,
            description=update.description,
            target_date=update.target_date,
            status=update.status or ProjectStatus.NOT_STARTED,
        )

    monkeypatch.setattr(projects, "update_owned_project", fake_update)
    response = client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "description": None,
            "target_date": None,
            "status": "IN_PROGRESS",
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    update = observed["update"]
    assert isinstance(update, ProjectUpdate)
    assert update.model_fields_set == {"description", "target_date", "status"}
    assert observed["project_id"] == project_id
    assert observed["user_id"] == user.id
    assert observed["session"] is session
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": str(uuid4())},
        {"id": str(uuid4())},
        {"created_at": "2026-09-01T00:00:00Z"},
        {"name": None},
        {"status": None},
        {"status": "ARCHIVED"},
    ],
    ids=("empty", "owner", "id", "timestamp", "null-name", "null-status", "archive"),
)
def test_project_update_rejects_invalid_shapes_before_service(
    payload: dict[str, object],
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect identity, timestamps, required values, and archive action."""

    _session, _user, lifecycle = project_request_context
    service = MagicMock()
    monkeypatch.setattr(projects, "update_owned_project", service)
    response = client.patch(f"/api/v1/projects/{uuid4()}", json=payload)

    assert response.status_code == 422
    service.assert_not_called()
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE),
            404,
            PROJECT_NOT_FOUND_MESSAGE,
        ),
        (ArchivedProjectError(ARCHIVED_PROJECT_MESSAGE), 409, ARCHIVED_PROJECT_MESSAGE),
        (
            ProjectDateOrderError(PROJECT_DATE_ORDER_ERROR_MESSAGE),
            422,
            PROJECT_DATE_ORDER_ERROR_MESSAGE,
        ),
    ],
    ids=("not-found", "archived", "date-order"),
)
def test_project_update_maps_only_safe_domain_errors(
    error: Exception,
    status_code: int,
    detail: str,
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate known domain outcomes without exposing persistence details."""

    _session, _user, lifecycle = project_request_context
    monkeypatch.setattr(
        projects,
        "update_owned_project",
        MagicMock(side_effect=error),
    )
    response = client.patch(
        f"/api/v1/projects/{uuid4()}",
        json={"name": "Valid"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert lifecycle == ["opened", "closed"]


def test_project_archive_delegates_idempotent_action_and_maps_404(
    client: TestClient,
    project_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep archive HTTP handling independent of first/repeat Service outcome."""

    session, user, lifecycle = project_request_context
    project_id = uuid4()
    observed: dict[str, object] = {}

    def fake_archive(
        received_project_id: UUID,
        user_id: UUID,
        received_session: Session,
    ) -> PublicProject:
        observed.update(
            project_id=received_project_id,
            user_id=user_id,
            session=received_session,
        )
        return make_public_project(
            project_id=received_project_id,
            status=ProjectStatus.ARCHIVED,
        )

    monkeypatch.setattr(projects, "archive_owned_project", fake_archive)
    response = client.post(f"/api/v1/projects/{project_id}/archive")

    assert response.status_code == 200
    assert response.json()["status"] == "ARCHIVED"
    assert observed == {
        "project_id": project_id,
        "user_id": user.id,
        "session": session,
    }
    assert lifecycle == ["opened", "closed"]

    app.dependency_overrides[get_session] = lambda: iter([session])
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(
        projects,
        "archive_owned_project",
        MagicMock(side_effect=ProjectNotFoundError(PROJECT_NOT_FOUND_MESSAGE)),
    )
    try:
        missing = client.post(f"/api/v1/projects/{uuid4()}/archive")
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)
    assert missing.status_code == 404
    assert missing.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}


def test_project_routes_are_versioned_and_have_only_planned_methods(
    client: TestClient,
) -> None:
    """Reject unversioned, delete, restore, and other unplanned surfaces."""

    project_id = uuid4()
    assert client.get("/projects").status_code == 404
    assert client.get("/api/projects").status_code == 404
    assert client.delete(f"/api/v1/projects/{project_id}").status_code == 405
    assert client.post(f"/api/v1/projects/{project_id}/restore").status_code == 404


def test_project_openapi_declares_exact_routes_schemas_and_security() -> None:
    """Keep the complete Stage 6 HTTP contract explicit and internal-field free."""

    schema = app.openapi()
    paths = schema["paths"]
    create = paths["/api/v1/projects"]["post"]
    listing = paths["/api/v1/projects"]["get"]
    detail = paths["/api/v1/projects/{project_id}"]["get"]
    update = paths["/api/v1/projects/{project_id}"]["patch"]
    archive = paths["/api/v1/projects/{project_id}/archive"]["post"]

    assert set(paths["/api/v1/projects"]) == {"get", "post"}
    assert set(paths["/api/v1/projects/{project_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/projects/{project_id}/archive"]) == {"post"}
    assert set(create["responses"]) == {"201", "401", "422"}
    assert set(detail["responses"]) == {"200", "401", "404", "422"}
    assert set(update["responses"]) == {"200", "401", "404", "409", "422"}
    assert set(archive["responses"]) == {"200", "401", "404", "422"}
    for operation in (create, listing, detail, update, archive):
        assert operation["security"] == [{"BearerAuth": []}]

    create_schema = schema["components"]["schemas"]["ProjectCreate"]
    update_schema = schema["components"]["schemas"]["ProjectUpdate"]
    public_schema = schema["components"]["schemas"]["PublicProject"]
    list_schema = schema["components"]["schemas"]["ProjectListResponse"]
    assert set(create_schema["properties"]) == {
        "name",
        "description",
        "start_date",
        "target_date",
    }
    assert set(update_schema["properties"]) == {
        "name",
        "description",
        "start_date",
        "target_date",
        "status",
    }
    assert set(public_schema["properties"]) == PUBLIC_FIELDS
    assert set(list_schema["properties"]) == {
        "items",
        "page",
        "page_size",
        "total",
        "pages",
    }
    assert create_schema["additionalProperties"] is False
    assert update_schema["additionalProperties"] is False
    assert "user_id" not in str((create_schema, update_schema, public_schema))

    parameters = {parameter["name"] for parameter in listing["parameters"]}
    assert parameters == {"page", "page_size", "include_archived"}
