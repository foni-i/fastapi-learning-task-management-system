"""Connection-free metadata tests for the foundational User model."""

from typing import cast

from sqlalchemy import DateTime, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DefaultClause, UniqueConstraint

from app.db.base import Base
from app.models import User

EXPECTED_COLUMNS = {"id", "email", "password_hash", "created_at", "updated_at"}


def test_user_remains_registered_in_shared_product_metadata() -> None:
    """Keep the exact User mapping after Project metadata is introduced."""

    assert User.metadata is Base.metadata
    assert set(Base.metadata.tables) == {"users", "projects", "tasks"}
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


def test_user_email_has_named_unique_constraint_without_index() -> None:
    """Expose the named final defense for canonical email values in metadata."""

    table = cast(Table, User.__table__)
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert table.c.email.unique in (None, False)
    assert table.c.email.index in (None, False)
    assert unique_constraints == {"uq_users_email": ("email",)}
    assert not table.indexes
