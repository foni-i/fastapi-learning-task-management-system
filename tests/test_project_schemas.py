"""Unit tests for strict Project request and public response contracts."""

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import ProjectStatus
from app.schemas.project import (
    PROJECT_ARCHIVE_ACTION_ERROR_MESSAGE,
    PROJECT_DATE_ORDER_ERROR_MESSAGE,
    PROJECT_TIMESTAMP_ERROR_MESSAGE,
    PROJECT_UPDATE_EMPTY_ERROR_MESSAGE,
    ProjectCreate,
    ProjectListResponse,
    ProjectUpdate,
    PublicProject,
    validate_project_date_order,
)

CREATE_FIELDS = {"name", "description", "start_date", "target_date"}
UPDATE_FIELDS = CREATE_FIELDS | {"status"}
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


def make_public_source(**changes: object) -> SimpleNamespace:
    """Build an ORM-like source containing public and internal attributes."""

    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": uuid4(),
        "name": "Learning plan",
        "description": None,
        "start_date": date(2026, 9, 1),
        "target_date": date(2026, 9, 30),
        "status": "NOT_STARTED",
        "created_at": datetime(2026, 8, 31, 9, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 31, 9, tzinfo=UTC),
        "internal_note": "not public",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_create_has_exact_fields_and_canonicalizes_text() -> None:
    """Trim bounded text without admitting ownership or lifecycle input."""

    project = ProjectCreate.model_validate(
        {"name": "  Agent Study  ", "description": "  roadmap  "}
    )
    blank = ProjectCreate.model_validate({"name": "Valid", "description": "  "})

    assert set(ProjectCreate.model_fields) == CREATE_FIELDS
    assert project.name == "Agent Study"
    assert project.description == "roadmap"
    assert blank.description is None


@pytest.mark.parametrize("name", [" ", "x" * 201])
def test_create_rejects_invalid_name_bounds(name: str) -> None:
    """Apply bounds after trimming the supplied name."""

    with pytest.raises(ValidationError):
        ProjectCreate.model_validate({"name": name})


def test_create_accepts_exact_text_boundaries() -> None:
    """Accept one/two-hundred name and two-thousand description characters."""

    assert ProjectCreate(name="x").name == "x"
    assert len(ProjectCreate(name="x" * 200).name) == 200
    assert (
        len(ProjectCreate(name="x", description="d" * 2000).description or "") == 2000
    )


def test_create_rejects_description_over_limit_and_extra_internal_fields() -> None:
    """Bound optional text and forbid status/ownership fields on create."""

    with pytest.raises(ValidationError):
        ProjectCreate(name="Valid", description="d" * 2001)
    for extra in ("status", "user_id", "id"):
        with pytest.raises(ValidationError):
            ProjectCreate.model_validate({"name": "Valid", extra: "forbidden"})


def test_create_and_helper_enforce_date_order() -> None:
    """Reject reversed complete date pairs but allow either null endpoint."""

    with pytest.raises(ValidationError) as exc_info:
        ProjectCreate(
            name="Valid",
            start_date=date(2026, 9, 2),
            target_date=date(2026, 9, 1),
        )
    assert PROJECT_DATE_ORDER_ERROR_MESSAGE in str(exc_info.value)
    validate_project_date_order(None, date(2026, 9, 1))
    validate_project_date_order(date(2026, 9, 1), None)


def test_update_has_exact_fields_and_preserves_missing_versus_null() -> None:
    """Expose explicitly cleared nullable fields through model_fields_set."""

    update = ProjectUpdate(description=None, target_date=None)

    assert set(ProjectUpdate.model_fields) == UPDATE_FIELDS
    assert update.model_fields_set == {"description", "target_date"}
    assert update.model_dump(exclude_unset=True) == {
        "description": None,
        "target_date": None,
    }


def test_update_rejects_empty_null_required_and_extra_payloads() -> None:
    """Reject no-op shape, explicit null required values, and internal fields."""

    with pytest.raises(ValidationError) as empty_error:
        ProjectUpdate()
    assert PROJECT_UPDATE_EMPTY_ERROR_MESSAGE in str(empty_error.value)

    for payload in ({"name": None}, {"status": None}, {"user_id": str(uuid4())}):
        with pytest.raises(ValidationError):
            ProjectUpdate.model_validate(payload)


@pytest.mark.parametrize(
    "status",
    [
        ProjectStatus.NOT_STARTED,
        ProjectStatus.IN_PROGRESS,
        ProjectStatus.COMPLETED,
    ],
)
def test_update_accepts_only_non_archived_statuses(status: ProjectStatus) -> None:
    """Keep ordinary status transitions separate from archive action."""

    assert ProjectUpdate(status=status).status is status

    with pytest.raises(ValidationError) as exc_info:
        ProjectUpdate(status=ProjectStatus.ARCHIVED)
    assert PROJECT_ARCHIVE_ACTION_ERROR_MESSAGE in str(exc_info.value)


def test_update_validates_only_a_complete_supplied_date_pair() -> None:
    """Leave persisted-state combinations to the Service helper boundary."""

    assert ProjectUpdate(target_date=date(2026, 9, 1)).target_date is not None
    with pytest.raises(ValidationError):
        ProjectUpdate(
            start_date=date(2026, 9, 2),
            target_date=date(2026, 9, 1),
        )


def test_public_project_serializes_exact_allowlist_from_attributes() -> None:
    """Exclude owner and internal attributes from dict and JSON output."""

    public = PublicProject.model_validate(make_public_source())

    assert set(PublicProject.model_fields) == PUBLIC_FIELDS
    assert set(public.model_dump()) == PUBLIC_FIELDS
    assert "user_id" not in public.model_dump_json()
    assert "internal_note" not in public.model_dump_json()
    assert public.status is ProjectStatus.NOT_STARTED


def test_public_project_requires_aware_time_and_normalizes_to_utc() -> None:
    """Reject naive values and preserve instant while emitting UTC."""

    with pytest.raises(ValidationError) as exc_info:
        PublicProject.model_validate(
            make_public_source(created_at=datetime(2026, 8, 31, 9))
        )
    assert PROJECT_TIMESTAMP_ERROR_MESSAGE in str(exc_info.value)

    offset = timezone(timedelta(hours=8))
    public = PublicProject.model_validate(
        make_public_source(
            created_at=datetime(2026, 8, 31, 9, tzinfo=offset),
            updated_at=datetime(2026, 8, 31, 10, tzinfo=offset),
        )
    )
    assert public.created_at == datetime(2026, 8, 31, 1, tzinfo=UTC)
    assert public.updated_at == datetime(2026, 8, 31, 2, tzinfo=UTC)
    assert '"created_at":"2026-08-31T01:00:00Z"' in public.model_dump_json()


def test_project_list_has_exact_consistent_bounded_metadata() -> None:
    """Keep page metadata deterministic and internally consistent."""

    item = PublicProject.model_validate(make_public_source())
    response = ProjectListResponse(
        items=[item], page=1, page_size=20, total=21, pages=2
    )
    empty = ProjectListResponse(items=[], page=1, page_size=20, total=0, pages=0)

    assert set(ProjectListResponse.model_fields) == {
        "items",
        "page",
        "page_size",
        "total",
        "pages",
    }
    assert response.pages == 2
    assert empty.pages == 0

    for payload in (
        {"items": [], "page": 0, "page_size": 20, "total": 0, "pages": 0},
        {"items": [], "page": 1, "page_size": 101, "total": 0, "pages": 0},
        {"items": [], "page": 1, "page_size": 20, "total": 21, "pages": 1},
    ):
        with pytest.raises(ValidationError):
            ProjectListResponse.model_validate(payload)
