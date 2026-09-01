"""End-to-end Stage 6 Project tests against dedicated PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ARCHIVED_PROJECT_MESSAGE, PROJECT_NOT_FOUND_MESSAGE
from app.db.session import get_session
from app.main import app
from app.models import Project
from tests.integration.test_authentication import (
    AuthenticationHarness,
    bearer,
    login,
    register_user,
)

pytestmark = pytest.mark.integration

PROJECTS_PATH = "/api/v1/projects"
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


class ProjectHarness(AuthenticationHarness):
    """Track committed Projects and delete only rows owned by this test."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.project_ids: set[UUID] = set()

    def track_project(self, response: dict[str, object]) -> UUID:
        project_id = UUID(str(response["id"]))
        self.project_ids.add(project_id)
        return project_id

    def project_snapshot(self, project_id: UUID) -> SimpleNamespace | None:
        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                return None
            return SimpleNamespace(
                id=project.id,
                user_id=project.user_id,
                name=project.name,
                description=project.description,
                start_date=project.start_date,
                target_date=project.target_date,
                status=project.status,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )

    def count_project(self, project_id: UUID) -> int:
        with self.session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.id == project_id)
            )
        assert count is not None
        return count

    def cleanup(self) -> None:
        if self.project_ids:
            with self.session_factory.begin() as session:
                session.execute(delete(Project).where(Project.id.in_(self.project_ids)))
        super().cleanup()


@pytest.fixture
def project_harness(integration_engine: Engine) -> Iterator[ProjectHarness]:
    harness = ProjectHarness(integration_engine)
    app.dependency_overrides[get_session] = harness.session_dependency
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_session, None)
        opened_ids = {id(session) for session in harness.opened_sessions}
        closed_ids = {id(session) for session in harness.closed_sessions}
        assert opened_ids == closed_ids
        harness.cleanup()


@pytest.fixture
def project_client(project_harness: ProjectHarness) -> Iterator[TestClient]:
    assert project_harness is not None
    with TestClient(app) as client:
        yield client


def create_project(
    client: TestClient,
    harness: ProjectHarness,
    token: str,
    *,
    name: str,
    description: str | None = None,
    start_date: str | None = None,
    target_date: str | None = None,
) -> tuple[UUID, dict[str, object]]:
    payload: dict[str, object] = {"name": name}
    if description is not None:
        payload["description"] = description
    if start_date is not None:
        payload["start_date"] = start_date
    if target_date is not None:
        payload["target_date"] = target_date
    response = client.post(PROJECTS_PATH, headers=bearer(token), json=payload)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == PUBLIC_FIELDS
    return harness.track_project(body), body


def test_project_create_detail_and_owner_isolation(
    project_client: TestClient,
    project_harness: ProjectHarness,
) -> None:
    """Prove owner-derived persistence and indistinguishable cross-user absence."""

    marker = project_harness.session_marker()
    owner_id, owner_email = register_user(
        project_client,
        project_harness,
        f"project-owner-{uuid4()}@example.com",
    )
    _other_id, other_email = register_user(
        project_client,
        project_harness,
        f"project-other-{uuid4()}@example.com",
    )
    owner_token = login(project_client, owner_email)
    other_token = login(project_client, other_email)

    project_id, created = create_project(
        project_client,
        project_harness,
        owner_token,
        name="  Agent learning plan  ",
        description="  PostgreSQL milestone  ",
        start_date="2026-09-01",
        target_date="2026-09-30",
    )
    assert created["name"] == "Agent learning plan"
    assert created["description"] == "PostgreSQL milestone"
    assert created["status"] == "NOT_STARTED"
    assert "user_id" not in created
    assert datetime.fromisoformat(str(created["created_at"])).tzinfo is not None
    assert datetime.fromisoformat(str(created["updated_at"])).tzinfo is not None

    stored = project_harness.project_snapshot(project_id)
    assert stored is not None
    assert stored.user_id == owner_id
    assert stored.name == "Agent learning plan"
    assert stored.start_date == date(2026, 9, 1)
    assert project_harness.count_project(project_id) == 1

    own = project_client.get(
        f"{PROJECTS_PATH}/{project_id}", headers=bearer(owner_token)
    )
    foreign = project_client.get(
        f"{PROJECTS_PATH}/{project_id}", headers=bearer(other_token)
    )
    missing = project_client.get(
        f"{PROJECTS_PATH}/{uuid4()}", headers=bearer(other_token)
    )
    assert own.status_code == 200
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}
    assert (
        project_client.get(
            f"{PROJECTS_PATH}/not-a-uuid", headers=bearer(owner_token)
        ).status_code
        == 422
    )
    unauthenticated = project_client.get(f"{PROJECTS_PATH}/{project_id}")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"

    rejected_owner = project_client.post(
        PROJECTS_PATH,
        headers=bearer(owner_token),
        json={"name": "Injected", "user_id": str(uuid4())},
    )
    assert rejected_owner.status_code == 422
    project_harness.assert_sessions_closed_since(marker, 11)


def test_project_list_pagination_order_and_archive_filter(
    project_client: TestClient,
    project_harness: ProjectHarness,
) -> None:
    """Prove fixed ordering, pagination, ownership, and archive visibility."""

    _owner_id, owner_email = register_user(
        project_client, project_harness, f"project-list-{uuid4()}@example.com"
    )
    _other_id, other_email = register_user(
        project_client, project_harness, f"project-list-other-{uuid4()}@example.com"
    )
    owner_token = login(project_client, owner_email)
    other_token = login(project_client, other_email)
    project_ids = [
        create_project(
            project_client, project_harness, owner_token, name=f"Page item {index}"
        )[0]
        for index in range(3)
    ]
    create_project(project_client, project_harness, other_token, name="Foreign item")

    base = datetime(2026, 9, 1, tzinfo=UTC)
    with project_harness.session_factory.begin() as session:
        for index, project_id in enumerate(project_ids):
            session.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(created_at=base + timedelta(minutes=index))
            )

    first = project_client.get(
        f"{PROJECTS_PATH}?page=1&page_size=2", headers=bearer(owner_token)
    )
    second = project_client.get(
        f"{PROJECTS_PATH}?page=2&page_size=2", headers=bearer(owner_token)
    )
    assert first.status_code == second.status_code == 200
    assert (first.json()["page"], first.json()["page_size"]) == (1, 2)
    assert first.json()["total"] == 3
    assert first.json()["pages"] == 2
    listed = [
        UUID(item["id"]) for item in first.json()["items"] + second.json()["items"]
    ]
    assert listed == list(reversed(project_ids))
    assert len(set(listed)) == 3
    assert all("user_id" not in item for item in first.json()["items"])

    archived_id = project_ids[1]
    archived = project_client.post(
        f"{PROJECTS_PATH}/{archived_id}/archive", headers=bearer(owner_token)
    )
    assert archived.status_code == 200
    default_list = project_client.get(PROJECTS_PATH, headers=bearer(owner_token)).json()
    full_list = project_client.get(
        f"{PROJECTS_PATH}?include_archived=true", headers=bearer(owner_token)
    ).json()
    assert default_list["total"] == 2
    assert full_list["total"] == 3
    assert archived_id not in {UUID(item["id"]) for item in default_list["items"]}
    assert archived_id in {UUID(item["id"]) for item in full_list["items"]}
    assert (
        project_client.get(
            f"{PROJECTS_PATH}?page_size=101", headers=bearer(owner_token)
        ).status_code
        == 422
    )


def test_project_patch_archive_idempotency_and_safe_failures(
    project_client: TestClient,
    project_harness: ProjectHarness,
) -> None:
    """Prove committed partial updates, no-ops, archive idempotency, and isolation."""

    owner_id, owner_email = register_user(
        project_client, project_harness, f"project-update-{uuid4()}@example.com"
    )
    _other_id, other_email = register_user(
        project_client, project_harness, f"project-update-other-{uuid4()}@example.com"
    )
    owner_token = login(project_client, owner_email)
    other_token = login(project_client, other_email)
    project_id, _body = create_project(
        project_client,
        project_harness,
        owner_token,
        name="Mutable",
        description="Clear me",
        start_date="2026-09-01",
        target_date="2026-09-30",
    )
    before = project_harness.project_snapshot(project_id)
    assert before is not None

    changed = project_client.patch(
        f"{PROJECTS_PATH}/{project_id}",
        headers=bearer(owner_token),
        json={
            "name": "Changed",
            "description": None,
            "start_date": None,
            "target_date": None,
            "status": "IN_PROGRESS",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["description"] is None
    assert changed.json()["start_date"] is None
    assert changed.json()["target_date"] is None
    assert changed.json()["status"] == "IN_PROGRESS"
    changed_at = datetime.fromisoformat(changed.json()["updated_at"])
    assert changed_at > before.updated_at

    noop = project_client.patch(
        f"{PROJECTS_PATH}/{project_id}",
        headers=bearer(owner_token),
        json={"name": "Changed"},
    )
    assert noop.status_code == 200
    assert datetime.fromisoformat(noop.json()["updated_at"]) == changed_at

    invalid = project_client.patch(
        f"{PROJECTS_PATH}/{project_id}",
        headers=bearer(owner_token),
        json={"start_date": "2026-10-02", "target_date": "2026-10-01"},
    )
    assert invalid.status_code == 422
    assert (
        project_client.patch(
            f"{PROJECTS_PATH}/{project_id}", headers=bearer(owner_token), json={}
        ).status_code
        == 422
    )
    assert (
        project_client.patch(
            f"{PROJECTS_PATH}/{project_id}",
            headers=bearer(owner_token),
            json={"user_id": str(owner_id)},
        ).status_code
        == 422
    )

    foreign = project_client.patch(
        f"{PROJECTS_PATH}/{project_id}",
        headers=bearer(other_token),
        json={"name": "Forbidden"},
    )
    assert foreign.status_code == 404
    assert foreign.json() == {"detail": PROJECT_NOT_FOUND_MESSAGE}
    preserved = project_harness.project_snapshot(project_id)
    assert preserved is not None
    assert preserved.name == "Changed"

    first_archive = project_client.post(
        f"{PROJECTS_PATH}/{project_id}/archive", headers=bearer(owner_token)
    )
    assert first_archive.status_code == 200
    first_updated_at = first_archive.json()["updated_at"]
    repeated_archive = project_client.post(
        f"{PROJECTS_PATH}/{project_id}/archive", headers=bearer(owner_token)
    )
    assert repeated_archive.status_code == 200
    assert repeated_archive.json()["updated_at"] == first_updated_at
    blocked = project_client.patch(
        f"{PROJECTS_PATH}/{project_id}",
        headers=bearer(owner_token),
        json={"name": "Blocked"},
    )
    assert blocked.status_code == 409
    assert blocked.json() == {"detail": ARCHIVED_PROJECT_MESSAGE}
    foreign_archive = project_client.post(
        f"{PROJECTS_PATH}/{project_id}/archive", headers=bearer(other_token)
    )
    missing_archive = project_client.post(
        f"{PROJECTS_PATH}/{uuid4()}/archive", headers=bearer(owner_token)
    )
    assert foreign_archive.status_code == missing_archive.status_code == 404
    assert (
        foreign_archive.json()
        == missing_archive.json()
        == {"detail": PROJECT_NOT_FOUND_MESSAGE}
    )
    assert (
        project_client.delete(
            f"{PROJECTS_PATH}/{project_id}", headers=bearer(owner_token)
        ).status_code
        == 405
    )
    assert (
        project_client.post(
            f"{PROJECTS_PATH}/{project_id}/restore", headers=bearer(owner_token)
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"name": "   "}, "ck_projects_name_not_blank"),
        ({"name": "Valid", "status": "INVALID"}, "ck_projects_status"),
        (
            {
                "name": "Valid",
                "start_date": date(2026, 9, 2),
                "target_date": date(2026, 9, 1),
            },
            "ck_projects_target_date_not_before_start_date",
        ),
        ({"name": "Valid", "user_id": uuid4()}, "fk_projects_user_id_users"),
    ],
    ids=("blank-name", "status", "date-order", "foreign-key"),
)
def test_named_project_constraints_reject_direct_invalid_writes(
    values: dict[str, object],
    constraint: str,
    project_client: TestClient,
    project_harness: ProjectHarness,
) -> None:
    """Use direct writes only to prove PostgreSQL is the final integrity boundary."""

    user_id, _email = register_user(
        project_client, project_harness, f"project-constraint-{uuid4()}@example.com"
    )
    payload: dict[str, object] = {"user_id": user_id, **values}
    with project_harness.session_factory() as session:
        with pytest.raises(IntegrityError) as exc_info:
            session.execute(insert(Project).values(**payload))
            session.flush()
        diagnostic = getattr(exc_info.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == constraint
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Project)) is not None
