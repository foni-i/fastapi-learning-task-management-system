"""Connection-free metadata tests for the foundational User model."""

from typing import cast

from sqlalchemy import DateTime, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DefaultClause, UniqueConstraint

from app.db.base import Base
from app.models import User

EXPECTED_COLUMNS = {"id", "email", "password_hash", "created_at", "updated_at"}


def test_user_is_the_only_registered_product_table() -> None:
    """Register only the users table in shared SQLAlchemy metadata."""

    assert User.metadata is Base.metadata
    assert set(Base.metadata.tables) == {"users"}
    assert set(User.__table__.columns.keys()) == EXPECTED_COLUMNS


def test_user_columns_have_exact_types_bounds_and_defaults() -> None:
    """Model UUID, strings, and UTC-capable timestamps without Python defaults."""

    table = cast(Table, User.__table__)
    identifier = table.c.id
    email = table.c.email
    password_hash = table.c.password_hash

    assert isinstance(identifier.type, postgresql.UUID)
    assert identifier.type.as_uuid is True
    assert identifier.primary_key is True
    assert identifier.nullable is False
    assert identifier.default is None
    assert isinstance(identifier.server_default, DefaultClause)
    assert str(identifier.server_default.arg) == "gen_random_uuid()"

    assert isinstance(email.type, String)
    assert email.type.length == 254
    assert email.nullable is False
    assert email.default is None
    assert email.server_default is None

    assert isinstance(password_hash.type, String)
    assert password_hash.type.length == 255
    assert password_hash.nullable is False
    assert password_hash.default is None
    assert password_hash.server_default is None

    for column_name in ("created_at", "updated_at"):
        timestamp = table.c[column_name]
        assert isinstance(timestamp.type, DateTime)
        assert timestamp.type.timezone is True
        assert timestamp.nullable is False
        assert timestamp.default is None
        assert isinstance(timestamp.server_default, DefaultClause)
        assert str(timestamp.server_default.arg) == "CURRENT_TIMESTAMP"


def test_user_email_has_no_unique_constraint_or_index_yet() -> None:
    """Defer every email uniqueness mechanism to Task 3.2."""

    table = cast(Table, User.__table__)

    assert table.c.email.unique in (None, False)
    assert table.c.email.index in (None, False)
    assert not any(isinstance(item, UniqueConstraint) for item in table.constraints)
    assert not table.indexes
