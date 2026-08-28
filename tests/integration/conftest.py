"""Safe synchronous PostgreSQL fixtures for integration tests."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.core.config import get_settings

POSTGRESQL_DRIVER = "postgresql+psycopg"
DEVELOPMENT_DATABASE_NAME = "stms"
MIGRATION_TEST_DATABASE_NAME = "stms_test"
MIGRATION_TEST_DATABASE_USER = "stms_test"
MIGRATION_TEST_HOSTS = frozenset({"127.0.0.1", "localhost"})
DEFAULT_MIGRATION_TEST_HOST_PORT = 5433


def get_migration_test_host_port(configured_port: str | None = None) -> int:
    """Return the explicit Compose test port or the cross-platform default."""

    raw_port = (
        configured_port
        if configured_port is not None
        else os.getenv("STMS_POSTGRES_TEST_PORT", str(DEFAULT_MIGRATION_TEST_HOST_PORT))
    )
    try:
        port = int(raw_port)
    except ValueError:
        raise pytest.UsageError(
            "STMS_POSTGRES_TEST_PORT must be an integer from 1 to 65535"
        ) from None

    if not 1 <= port <= 65535:
        raise pytest.UsageError(
            "STMS_POSTGRES_TEST_PORT must be an integer from 1 to 65535"
        )

    return port


def validate_test_database_url(
    test_database_url: str | None,
    development_database_url: str | None,
) -> URL:
    """Reject missing or unsafe test targets before creating an Engine."""

    if not test_database_url:
        raise pytest.UsageError(
            "integration tests require an explicit STMS_TEST_DATABASE_URL"
        )

    try:
        test_url = make_url(test_database_url)
    except ArgumentError, ValueError:
        raise pytest.UsageError(
            "STMS_TEST_DATABASE_URL must be a valid PostgreSQL test URL"
        ) from None

    if test_url.drivername != POSTGRESQL_DRIVER:
        raise pytest.UsageError("STMS_TEST_DATABASE_URL must use postgresql+psycopg")
    if not test_url.host or not test_url.database:
        raise pytest.UsageError(
            "STMS_TEST_DATABASE_URL must include a host and database name"
        )

    database_name = test_url.database.lower()
    if database_name == DEVELOPMENT_DATABASE_NAME or not database_name.endswith(
        "_test"
    ):
        raise pytest.UsageError(
            "STMS_TEST_DATABASE_URL must name an explicit *_test database"
        )

    if development_database_url:
        try:
            development_url = make_url(development_database_url)
        except ArgumentError, ValueError:
            raise pytest.UsageError(
                "STMS_DATABASE_URL must be valid when integration tests run"
            ) from None

        if test_url == development_url:
            raise pytest.UsageError(
                "integration tests refuse the configured development database"
            )
        if (
            test_url.host == development_url.host
            and test_url.port == development_url.port
        ):
            raise pytest.UsageError(
                "test and development databases must use isolated host ports"
            )

    return test_url


def validate_migration_test_target(
    test_database_url: str | None,
    expected_host_port: int | None = None,
) -> URL:
    """Restrict schema-changing tests to the dedicated Compose test service."""

    test_url = validate_test_database_url(test_database_url, None)
    host_port = (
        expected_host_port
        if expected_host_port is not None
        else get_migration_test_host_port()
    )
    if (
        test_url.host not in MIGRATION_TEST_HOSTS
        or test_url.database != MIGRATION_TEST_DATABASE_NAME
        or test_url.username != MIGRATION_TEST_DATABASE_USER
        or test_url.port != host_port
    ):
        raise pytest.UsageError(
            "migration tests require the dedicated stms_test service on the "
            "configured test port"
        )

    return test_url


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    """Validate and expose the dedicated URL without displaying its password."""

    configured_development_url = get_settings().database_url
    development_database_url = (
        configured_development_url.get_secret_value()
        if configured_development_url is not None
        else None
    )
    return validate_test_database_url(
        os.getenv("STMS_TEST_DATABASE_URL"),
        development_database_url,
    )


@pytest.fixture(scope="session")
def integration_engine(test_database_url: URL) -> Iterator[Engine]:
    """Create one synchronous Engine and always dispose its connection pool."""

    engine = create_engine(test_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migration_test_host_port() -> int:
    """Expose the validated host port shared by Compose and safety checks."""

    return get_migration_test_host_port()


@pytest.fixture(scope="session")
def migration_test_database_url(
    test_database_url: URL,
    migration_test_host_port: int,
) -> URL:
    """Revalidate the exact schema-changing target before migration tests."""

    return validate_migration_test_target(
        test_database_url.render_as_string(hide_password=False),
        migration_test_host_port,
    )


@pytest.fixture
def db_connection(integration_engine: Engine) -> Iterator[Connection]:
    """Give each test an isolated connection and rolled-back transaction."""

    with integration_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                transaction.rollback()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    """Bind a short-lived Session to the test-owned transaction."""

    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
