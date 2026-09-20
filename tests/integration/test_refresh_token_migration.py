"""Guarded PostgreSQL storage constraints and the narrow Task 5.1 round trip."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, delete, func, insert, inspect, select, text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql import Executable

from alembic import command
from app.core.config import get_settings
from app.models import Project, RefreshToken, User
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration
PARENT_REVISION = "e3b7c2d9a410"
REFRESH_REVISION = "86cd95365562"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"
CREATED_AT = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def require_dedicated_storage_target(migration_test_database_url: URL) -> None:
    """Apply the exact Compose target guard to every test in this module."""

    validate_migration_test_target(
        migration_test_database_url.render_as_string(hide_password=False)
    )


def create_test_user(connection: Connection) -> UUID:
    identifier = uuid4()
    connection.execute(
        insert(User).values(
            id=identifier,
            email=f"refresh-storage-{identifier}@example.test",
            password_hash="test-only-not-a-password-hash",
        )
    )
    return identifier


def token_values(owner: UUID) -> dict[str, object]:
    """Supply synthetic storage data, never a usable issued credential."""

    return {
        "id": uuid4(),
        "user_id": owner,
        "token_hash": f"{uuid4().int:064x}",
        "created_at": CREATED_AT,
        "expires_at": CREATED_AT + timedelta(days=1),
        "revoked_at": None,
    }


def assert_rejected(
    connection: Connection,
    statement: Executable,
    *,
    sqlstate: str,
    constraint: str | None = None,
) -> None:
    """Inspect bounded database diagnostics without rendering SQL or values."""

    state = None
    name = None
    try:
        with connection.begin_nested():
            connection.execute(statement)
    except DBAPIError as error:
        state = getattr(error.orig, "sqlstate", None)
        name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    assert state == sqlstate
    if constraint is not None:
        assert name == constraint
    # A failed statement must not poison the enclosing test transaction.
    assert connection.scalar(select(1)) == 1


def assert_table_contract(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("refresh_tokens")}
    assert set(columns) == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "revoked_at",
    }
    for name, column in columns.items():
        assert column["nullable"] is (name == "revoked_at")
        if name in ("id", "user_id"):
            assert str(column["type"]) == "UUID"
        elif name == "token_hash":
            assert str(column["type"]) == "VARCHAR(64)"
        else:
            assert isinstance(column["type"], DateTime)
            assert column["type"].timezone is True
        if name == "id":
            assert "gen_random_uuid()" in str(column["default"])
        elif name == "created_at":
            assert column["default"] == "CURRENT_TIMESTAMP"
        else:
            assert column["default"] is None
    primary = inspector.get_pk_constraint("refresh_tokens")
    assert primary["name"] == "pk_refresh_tokens"
    assert primary["constrained_columns"] == ["id"]
    unique = inspector.get_unique_constraints("refresh_tokens")
    assert [(c["name"], c["column_names"]) for c in unique] == [
        ("uq_refresh_tokens_token_hash", ["token_hash"])
    ]
    foreign = inspector.get_foreign_keys("refresh_tokens")
    assert len(foreign) == 1
    assert foreign[0]["name"] == "fk_refresh_tokens_user_id_users"
    assert foreign[0]["constrained_columns"] == ["user_id"]
    assert foreign[0]["referred_table"] == "users"
    assert foreign[0]["referred_columns"] == ["id"]
    assert foreign[0].get("options", {}).get("ondelete", "NO ACTION") == "NO ACTION"
    assert {c["name"] for c in inspector.get_check_constraints("refresh_tokens")} == {
        "ck_refresh_tokens_token_hash_format",
        "ck_refresh_tokens_expiry_after_creation",
        "ck_refresh_tokens_revocation_not_before_creation",
    }
    indexes = inspector.get_indexes("refresh_tokens")
    assert {i["name"] for i in indexes} == {
        "ix_refresh_tokens_user_id",
        "uq_refresh_tokens_token_hash",
    }
    assert {(i["name"], tuple(i["column_names"]), i["unique"]) for i in indexes} == {
        ("ix_refresh_tokens_user_id", ("user_id",), False),
        ("uq_refresh_tokens_token_hash", ("token_hash",), True),
    }


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_refresh_migration_round_trip_preserves_existing_objects_and_rows(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = migration_test_database_url.render_as_string(hide_password=False)
    config = Config(str(CONFIG_PATH))
    monkeypatch.setenv("STMS_DATABASE_URL", target)
    get_settings.cache_clear()
    owner: UUID | None = None
    project_id = uuid4()
    try:
        validate_migration_test_target(target)
        command.downgrade(config, PARENT_REVISION)
        assert current_revision(integration_engine) == PARENT_REVISION
        before_tables = set(inspect(integration_engine).get_table_names())
        assert "refresh_tokens" not in before_tables
        with integration_engine.begin() as connection:
            owner = create_test_user(connection)
            connection.execute(
                insert(Project).values(
                    id=project_id, user_id=owner, name="Task 5.1 migration sentinel"
                )
            )
            before_user = connection.execute(select(User).where(User.id == owner)).one()
            before_project = connection.execute(
                select(Project).where(Project.id == project_id)
            ).one()
            before_extensions = connection.execute(
                text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
            ).all()

        for direction in ("upgrade", "downgrade", "upgrade"):
            validate_migration_test_target(target)
            if direction == "upgrade":
                command.upgrade(config, REFRESH_REVISION)
                assert current_revision(integration_engine) == REFRESH_REVISION
                assert set(inspect(integration_engine).get_table_names()) == (
                    before_tables | {"refresh_tokens"}
                )
                assert_table_contract(integration_engine)
                with integration_engine.begin() as connection:
                    # This committed synthetic row is deliberately lost at downgrade.
                    assert (
                        connection.scalar(
                            select(func.count()).select_from(RefreshToken)
                        )
                        == 0
                    )
                    connection.execute(
                        insert(RefreshToken).values(**token_values(owner))
                    )
            else:
                command.downgrade(config, PARENT_REVISION)
                assert current_revision(integration_engine) == PARENT_REVISION
                assert (
                    set(inspect(integration_engine).get_table_names()) == before_tables
                )
            with integration_engine.connect() as connection:
                # Compare booleans so assertion rendering cannot disclose private fields.
                same_user = (
                    connection.execute(select(User).where(User.id == owner)).one()
                    == before_user
                )
                same_project = (
                    connection.execute(
                        select(Project).where(Project.id == project_id)
                    ).one()
                    == before_project
                )
                assert same_user
                assert same_project
                assert (
                    connection.execute(
                        text(
                            "SELECT extname, extversion FROM pg_extension ORDER BY extname"
                        )
                    ).all()
                    == before_extensions
                )
        command.check(config)
    finally:
        original_failure = sys.exception()
        try:
            validate_migration_test_target(target)
            command.upgrade(config, "head")
            with integration_engine.begin() as connection:
                if owner is not None:
                    connection.execute(
                        delete(RefreshToken).where(RefreshToken.user_id == owner)
                    )
                    connection.execute(
                        delete(Project).where(
                            Project.id == project_id, Project.user_id == owner
                        )
                    )
                    connection.execute(delete(User).where(User.id == owner))
        except Exception:
            message = "Task 5.1 test cleanup/head restoration failed; inspect the dedicated test database"
            if original_failure is not None:
                original_failure.add_note(message)
            else:
                pytest.fail(message, pytrace=False)
        finally:
            get_settings.cache_clear()


def test_database_defaults_and_multiple_credentials_per_owner(
    db_connection: Connection,
) -> None:
    owner_a = create_test_user(db_connection)
    owner_b = create_test_user(db_connection)
    created = db_connection.scalar(select(func.current_timestamp()))
    assert isinstance(created, datetime)
    expires = created + timedelta(days=1)
    ids: set[UUID] = set()
    for owner in (owner_a, owner_a, owner_b):
        row = db_connection.execute(
            insert(RefreshToken)
            .values(
                user_id=owner,
                token_hash=f"{uuid4().int:064x}",
                expires_at=expires,
            )
            .returning(
                RefreshToken.id,
                RefreshToken.user_id,
                RefreshToken.created_at,
                RefreshToken.expires_at,
                RefreshToken.revoked_at,
            )
        ).one()
        assert isinstance(row.id, UUID)
        assert row.id not in ids
        ids.add(row.id)
        assert row.user_id == owner
        assert row.created_at == created
        assert row.created_at.utcoffset() == timedelta(0)
        assert row.expires_at == expires
        assert row.revoked_at is None


@pytest.mark.parametrize(
    "cross_owner", [False, True], ids=["same-owner", "other-owner"]
)
def test_digest_uniqueness_is_global(
    db_connection: Connection, cross_owner: bool
) -> None:
    owner = create_test_user(db_connection)
    values = token_values(owner)
    db_connection.execute(insert(RefreshToken).values(**values))
    duplicate = dict(values, id=uuid4())
    if cross_owner:
        duplicate["user_id"] = create_test_user(db_connection)
    assert_rejected(
        db_connection,
        insert(RefreshToken).values(**duplicate),
        sqlstate="23505",
        constraint="uq_refresh_tokens_token_hash",
    )
    db_connection.execute(insert(RefreshToken).values(**token_values(owner)))


@pytest.mark.parametrize(
    ("value", "sqlstate"),
    [
        ("", "23514"),
        ("a" * 63, "23514"),
        ("a" * 65, "22001"),
        ("A" * 64, "23514"),
        ("g" * 64, "23514"),
    ],
    ids=["empty", "short", "long", "uppercase", "non-hex"],
)
def test_malformed_digest_is_rejected(
    db_connection: Connection, value: str, sqlstate: str
) -> None:
    owner = create_test_user(db_connection)
    values = dict(token_values(owner), token_hash=value)
    assert_rejected(
        db_connection,
        insert(RefreshToken).values(**values),
        sqlstate=sqlstate,
        constraint="ck_refresh_tokens_token_hash_format"
        if sqlstate == "23514"
        else None,
    )
    db_connection.execute(insert(RefreshToken).values(**token_values(owner)))


@pytest.mark.parametrize(
    "column", ["id", "user_id", "token_hash", "created_at", "expires_at"]
)
def test_required_columns_reject_explicit_null(
    db_connection: Connection, column: str
) -> None:
    owner = create_test_user(db_connection)
    values = token_values(owner)
    values[column] = None
    assert_rejected(
        db_connection, insert(RefreshToken).values(**values), sqlstate="23502"
    )
    db_connection.execute(insert(RefreshToken).values(**token_values(owner)))


@pytest.mark.parametrize(
    ("column", "offset", "constraint"),
    [
        ("expires_at", -1, "ck_refresh_tokens_expiry_after_creation"),
        ("expires_at", 0, "ck_refresh_tokens_expiry_after_creation"),
        ("revoked_at", -1, "ck_refresh_tokens_revocation_not_before_creation"),
    ],
    ids=["negative-lifetime", "zero-lifetime", "revoked-before-created"],
)
def test_invalid_lifetime_is_rejected(
    db_connection: Connection, column: str, offset: int, constraint: str
) -> None:
    owner = create_test_user(db_connection)
    values = token_values(owner)
    values[column] = CREATED_AT + timedelta(microseconds=offset)
    assert_rejected(
        db_connection,
        insert(RefreshToken).values(**values),
        sqlstate="23514",
        constraint=constraint,
    )
    db_connection.execute(insert(RefreshToken).values(**token_values(owner)))


@pytest.mark.parametrize(
    "revoked",
    [None, CREATED_AT, CREATED_AT + timedelta(days=2)],
    ids=["unrevoked", "at-creation", "after-expiry"],
)
def test_allowed_revocation_boundaries(
    db_connection: Connection, revoked: datetime | None
) -> None:
    owner = create_test_user(db_connection)
    values = dict(token_values(owner), revoked_at=revoked)
    actual = db_connection.scalar(
        insert(RefreshToken).values(**values).returning(RefreshToken.revoked_at)
    )
    assert actual == revoked


def test_foreign_key_rejects_missing_owner_and_owner_deletion(
    db_connection: Connection,
) -> None:
    assert_rejected(
        db_connection,
        insert(RefreshToken).values(**token_values(uuid4())),
        sqlstate="23503",
        constraint="fk_refresh_tokens_user_id_users",
    )
    owner = create_test_user(db_connection)
    db_connection.execute(insert(RefreshToken).values(**token_values(owner)))
    assert_rejected(
        db_connection,
        delete(User).where(User.id == owner),
        sqlstate="23503",
        constraint="fk_refresh_tokens_user_id_users",
    )
    assert (
        db_connection.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == owner)
        )
        == 1
    )
    assert db_connection.scalar(select(User.id).where(User.id == owner)) == owner
