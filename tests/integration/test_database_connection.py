"""Real PostgreSQL connectivity and transaction-isolation tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

EXPECTED_DATABASE = "stms_test"
EXPECTED_USER = "stms_test"
EXPECTED_TEST_HOST_PORT = 5433
TRANSACTION_PROBE_TABLE = "task_2_5_transaction_probe"


def test_connects_to_dedicated_postgresql_service(
    db_session: Session,
    integration_engine: Engine,
    test_database_url: URL,
) -> None:
    """Prove the configured synchronous target is the isolated Compose service."""

    row = db_session.execute(text("SELECT 1, current_database(), current_user")).one()

    assert row[0] == 1
    assert row[1] == EXPECTED_DATABASE
    assert row[2] == EXPECTED_USER
    assert integration_engine.dialect.is_async is False
    assert test_database_url.drivername == "postgresql+psycopg"
    assert test_database_url.port == EXPECTED_TEST_HOST_PORT
    assert test_database_url.port != 5432


def test_transactional_test_data_is_rolled_back(
    db_connection: Connection,
) -> None:
    """Create test-only DDL and rows that the fixture must roll back."""

    table_name = db_connection.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": TRANSACTION_PROBE_TABLE},
    ).scalar_one_or_none()
    assert table_name is None

    db_connection.execute(
        text(f"CREATE TABLE {TRANSACTION_PROBE_TABLE} (value integer NOT NULL)")
    )
    db_connection.execute(
        text(f"INSERT INTO {TRANSACTION_PROBE_TABLE} (value) VALUES (42)")
    )
    assert (
        db_connection.execute(
            text(f"SELECT value FROM {TRANSACTION_PROBE_TABLE}")
        ).scalar_one()
        == 42
    )


def test_new_test_cannot_see_previous_transaction_data(
    db_connection: Connection,
) -> None:
    """A fresh test transaction starts without another test's temporary data."""

    table_name = db_connection.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": TRANSACTION_PROBE_TABLE},
    ).scalar_one_or_none()
    assert table_name is None
