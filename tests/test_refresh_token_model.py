"""Connection-free contracts for private refresh-token storage."""

from typing import cast

from sqlalchemy import CheckConstraint, DateTime, String, Table, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DefaultClause, ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.main import create_app
from app.models import RefreshToken


def test_refresh_token_has_only_private_storage_columns() -> None:
    assert RefreshToken.metadata is Base.metadata
    assert Base.metadata.tables["refresh_tokens"] is RefreshToken.__table__
    assert set(RefreshToken.__table__.columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "revoked_at",
    }
    assert not inspect(RefreshToken).relationships


def test_refresh_token_types_nullability_and_server_defaults() -> None:
    table = cast(Table, RefreshToken.__table__)
    for name in ("id", "user_id"):
        column = table.c[name]
        assert isinstance(column.type, postgresql.UUID)
        assert column.type.as_uuid is True
    assert isinstance(table.c.token_hash.type, String)
    assert table.c.token_hash.type.length == 64
    for name in ("created_at", "expires_at", "revoked_at"):
        timestamp_type = table.c[name].type
        assert isinstance(timestamp_type, DateTime)
        assert timestamp_type.timezone is True
    for column in table.c:
        assert column.nullable is (column.name == "revoked_at")
        assert column.default is None
        assert column.onupdate is None
        assert column.server_onupdate is None
        if column.name in ("id", "created_at"):
            assert isinstance(column.server_default, DefaultClause)
            assert (
                str(column.server_default.arg)
                == {"id": "gen_random_uuid()", "created_at": "CURRENT_TIMESTAMP"}[
                    column.name
                ]
            )
        else:
            assert column.server_default is None


def test_refresh_token_integrity_and_minimal_owner_index() -> None:
    table = cast(Table, RefreshToken.__table__)
    assert table.primary_key.name == "pk_refresh_tokens"
    assert tuple(table.primary_key.columns.keys()) == ("id",)
    unique = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert [(c.name, tuple(c.columns.keys())) for c in unique] == [
        ("uq_refresh_tokens_token_hash", ("token_hash",))
    ]
    foreign = [c for c in table.constraints if isinstance(c, ForeignKeyConstraint)]
    assert len(foreign) == 1
    assert foreign[0].name == "fk_refresh_tokens_user_id_users"
    assert tuple(foreign[0].columns.keys()) == ("user_id",)
    assert [element.target_fullname for element in foreign[0].elements] == ["users.id"]
    assert foreign[0].ondelete is None
    assert foreign[0].onupdate is None
    assert {
        c.name: str(c.sqltext)
        for c in table.constraints
        if isinstance(c, CheckConstraint)
    } == {
        "ck_refresh_tokens_token_hash_format": "token_hash ~ '^[0-9a-f]{64}$'",
        "ck_refresh_tokens_expiry_after_creation": "expires_at > created_at",
        "ck_refresh_tokens_revocation_not_before_creation": (
            "revoked_at IS NULL OR revoked_at >= created_at"
        ),
    }
    assert [(i.name, tuple(i.columns.keys()), i.unique) for i in table.indexes] == [
        ("ix_refresh_tokens_user_id", ("user_id",), False)
    ]


def test_private_refresh_storage_does_not_expand_public_openapi() -> None:
    document = create_app().openapi()
    schemas = document["components"]["schemas"]
    assert set(schemas["TokenPairResponse"]["properties"]) == {
        "access_token",
        "token_type",
        "refresh_token",
        "refresh_expires_at",
    }
    for schema in schemas.values():
        assert "token_hash" not in schema.get("properties", {})
    assert "RefreshToken" not in schemas
    assert "/api/v1/auth/refresh" in document["paths"]
    assert "/api/v1/auth/logout" in document["paths"]
