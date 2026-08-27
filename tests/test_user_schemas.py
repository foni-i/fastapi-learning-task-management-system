"""Unit tests for registration input and public user output schemas."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.core.email_normalization import INVALID_EMAIL_MESSAGE
from app.models import User
from app.schemas.user import (
    TIMEZONE_ERROR_MESSAGE,
    PublicUser,
    UserRegistrationRequest,
)

REQUEST_FIELDS = {"email", "password"}
PUBLIC_USER_FIELDS = {"id", "email", "created_at", "updated_at"}


@pytest.mark.parametrize(
    ("raw_email", "expected"),
    [
        ("  User@Example.COM\t", "user@example.com"),
        ("Mixed.Case@EXAMPLE.COM", "mixed.case@example.com"),
        ("Straße@Example.com", "strasse@example.com"),
        ("User@例子.测试", "user@例子.测试"),
        ("User@xn--fsqu00a.xn--0zwm56d", "user@例子.测试"),
    ],
)
def test_registration_request_stores_canonical_email(
    raw_email: str,
    expected: str,
) -> None:
    """Reuse Task 3.2 normalization for every supported email representation."""

    request = UserRegistrationRequest.model_validate(
        {"email": raw_email, "password": "unchanged-secret"}
    )

    assert request.email == expected
    assert set(type(request).model_fields) == REQUEST_FIELDS


@pytest.mark.parametrize(
    "raw_email",
    [
        "",
        "  \t\r\n",
        "missing-at.example.com",
        "@example.com",
        "user@",
        f"{'a' * 64}@{'b' * 63}.{'c' * 63}.{'d' * 62}",
    ],
)
def test_registration_request_rejects_invalid_email_without_echoing_input(
    raw_email: str,
) -> None:
    """Expose one safe validation message without retaining raw email input."""

    with pytest.raises(ValidationError) as exc_info:
        UserRegistrationRequest.model_validate(
            {"email": raw_email, "password": "non-disclosed-password"}
        )

    error_text = str(exc_info.value)
    assert INVALID_EMAIL_MESSAGE in error_text
    assert "non-disclosed-password" not in error_text
    if raw_email:
        assert raw_email not in error_text


def test_registration_request_rejects_extra_fields_without_secret_disclosure() -> None:
    """Accept exactly email/password and hide the password on validation failure."""

    plaintext = "Secret With Spaces"

    with pytest.raises(ValidationError) as exc_info:
        UserRegistrationRequest.model_validate(
            {
                "email": "user@example.com",
                "password": plaintext,
                "role": "administrator",
            }
        )

    error_text = str(exc_info.value)
    assert "Extra inputs are not permitted" in error_text
    assert plaintext not in error_text


def test_registration_password_is_secret_aware_and_preserved_exactly() -> None:
    """Keep password input unchanged while redacting model representations."""

    plaintext = "  Mixed CASE 密码  "
    request = UserRegistrationRequest.model_validate(
        {
            "email": "user@example.com",
            "password": plaintext,
        }
    )

    assert isinstance(request.password, SecretStr)
    assert request.password.get_secret_value() == plaintext
    assert plaintext not in repr(request)
    assert plaintext not in str(request)
    assert plaintext not in request.model_dump_json()
    assert "**********" in request.model_dump_json()


@pytest.mark.parametrize("password", ["x" * 11, "x" * 129])
def test_registration_schema_defers_password_length_policy(password: str) -> None:
    """Prove the 12-128 policy has not been implemented before Task 3.4."""

    request = UserRegistrationRequest.model_validate(
        {"email": "user@example.com", "password": password}
    )

    assert request.password.get_secret_value() == password


def test_public_user_has_exact_typed_field_contract() -> None:
    """Keep UUID and the four-field public response allowlist explicit."""

    assert set(PublicUser.model_fields) == PUBLIC_USER_FIELDS
    assert PublicUser.model_fields["id"].annotation is UUID
    assert PublicUser.model_fields["email"].annotation is str
    assert PublicUser.model_fields["created_at"].annotation is datetime
    assert PublicUser.model_fields["updated_at"].annotation is datetime


def test_public_user_serializes_orm_attributes_without_internal_fields() -> None:
    """Read a User instance while excluding its password hash by construction."""

    identifier = uuid4()
    timestamp = datetime(2026, 8, 26, 8, 30, tzinfo=UTC)
    password_hash = "internal-test-hash-that-must-not-be-public"
    user = User(
        id=identifier,
        email="User@EXAMPLE.COM",
        password_hash=password_hash,
        created_at=timestamp,
        updated_at=timestamp,
    )

    public_user = PublicUser.model_validate(user)
    dumped = public_user.model_dump()
    serialized = public_user.model_dump_json()

    assert dumped == {
        "id": identifier,
        "email": "user@example.com",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    assert set(dumped) == PUBLIC_USER_FIELDS
    assert "password" not in serialized
    assert "password_hash" not in serialized
    assert password_hash not in serialized
    assert password_hash not in repr(public_user)


@pytest.mark.parametrize("field_name", ["created_at", "updated_at"])
def test_public_user_rejects_naive_timestamps(field_name: str) -> None:
    """Reject public datetimes without a usable UTC offset."""

    values = {
        "id": uuid4(),
        "email": "user@example.com",
        "created_at": datetime(2026, 8, 26, 8, 30, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 26, 8, 30, tzinfo=UTC),
    }
    values[field_name] = datetime(2026, 8, 26, 8, 30)

    with pytest.raises(ValidationError) as exc_info:
        PublicUser.model_validate(values)

    error_text = str(exc_info.value)
    assert TIMEZONE_ERROR_MESSAGE in error_text
    assert "2026-08-26" not in error_text


def test_public_user_normalizes_aware_timestamps_to_utc() -> None:
    """Convert offset-aware values to the public UTC convention."""

    east_eight = timezone(timedelta(hours=8))
    local_timestamp = datetime(2026, 8, 26, 16, 30, tzinfo=east_eight)
    source = SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        created_at=local_timestamp,
        updated_at=local_timestamp,
        password_hash="not-public",
    )

    public_user = PublicUser.model_validate(source)
    expected_utc = datetime(2026, 8, 26, 8, 30, tzinfo=UTC)
    serialized = public_user.model_dump_json()

    assert public_user.created_at == expected_utc
    assert public_user.updated_at == expected_utc
    assert public_user.created_at.tzinfo is UTC
    assert public_user.updated_at.tzinfo is UTC
    assert "2026-08-26T08:30:00Z" in serialized
    assert "password" not in serialized
