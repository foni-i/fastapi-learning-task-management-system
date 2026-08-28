"""Connection-free tests for integration database safety guards."""

import pytest

from tests.integration.conftest import (
    get_migration_test_host_port,
    validate_migration_test_target,
    validate_test_database_url,
)

SAFE_TEST_URL = "postgresql+psycopg://stms_test:test-password@127.0.0.1:5433/stms_test"
OVERRIDE_TEST_URL = (
    "postgresql+psycopg://stms_test:test-password@127.0.0.1:15433/stms_test"
)
SAFE_DEVELOPMENT_URL = "postgresql+psycopg://stms_dev:dev-password@127.0.0.1:5432/stms"


def test_accepts_isolated_postgresql_test_url() -> None:
    """A dedicated Psycopg URL on the test port passes without connecting."""

    parsed_url = validate_test_database_url(SAFE_TEST_URL, SAFE_DEVELOPMENT_URL)

    assert parsed_url.database == "stms_test"
    assert parsed_url.port == 5433


@pytest.mark.parametrize(
    ("test_url", "development_url", "message"),
    [
        (None, SAFE_DEVELOPMENT_URL, "explicit STMS_TEST_DATABASE_URL"),
        (
            "sqlite:///stms_test.db",
            SAFE_DEVELOPMENT_URL,
            r"must use postgresql\+psycopg",
        ),
        (
            "postgresql+psycopg://stms:password@127.0.0.1:5432/stms",
            SAFE_DEVELOPMENT_URL,
            r"explicit \*_test database",
        ),
        (
            SAFE_TEST_URL,
            SAFE_TEST_URL,
            "configured development database",
        ),
        (
            "postgresql+psycopg://stms_test:password@127.0.0.1:5432/stms_test",
            SAFE_DEVELOPMENT_URL,
            "isolated host ports",
        ),
    ],
)
def test_rejects_unsafe_target_without_revealing_credentials(
    test_url: str | None,
    development_url: str | None,
    message: str,
) -> None:
    """Unsafe configuration fails before Engine or connection construction."""

    with pytest.raises(pytest.UsageError, match=message) as exc_info:
        validate_test_database_url(test_url, development_url)

    error = str(exc_info.value)
    assert "password" not in error
    assert "@" not in error


def test_accepts_exact_default_migration_test_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema-changing commands may target only the dedicated Compose database."""

    monkeypatch.delenv("STMS_POSTGRES_TEST_PORT", raising=False)
    parsed_url = validate_migration_test_target(SAFE_TEST_URL)

    assert parsed_url.database == "stms_test"
    assert parsed_url.username == "stms_test"
    assert parsed_url.port == 5433


def test_accepts_explicit_migration_test_port_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the exact-port guard when Windows requires another host port."""

    monkeypatch.setenv("STMS_POSTGRES_TEST_PORT", "15433")

    parsed_url = validate_migration_test_target(OVERRIDE_TEST_URL)

    assert parsed_url.port == 15433


def test_rejects_url_that_does_not_match_configured_test_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require Compose and migration URLs to select the same host port."""

    monkeypatch.setenv("STMS_POSTGRES_TEST_PORT", "15433")

    with pytest.raises(pytest.UsageError, match="configured test port"):
        validate_migration_test_target(SAFE_TEST_URL)


@pytest.mark.parametrize("configured_port", ["invalid", "0", "-1", "65536"])
def test_rejects_invalid_configured_test_port_without_echoing_it(
    configured_port: str,
) -> None:
    """Fail before database access with one bounded, non-reflective error."""

    with pytest.raises(pytest.UsageError) as exc_info:
        get_migration_test_host_port(configured_port)

    error = str(exc_info.value)
    assert error == "STMS_POSTGRES_TEST_PORT must be an integer from 1 to 65535"
    assert configured_port not in error


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://stms_test:password@127.0.0.1:5432/stms_test",
        "postgresql+psycopg://wrong_user:password@127.0.0.1:5433/stms_test",
        "postgresql+psycopg://stms_test:password@127.0.0.1:5433/other_test",
        "postgresql+psycopg://stms_test:password@db.example.com:5433/stms_test",
    ],
)
def test_rejects_non_dedicated_migration_target(database_url: str) -> None:
    """A test-like name alone cannot authorize schema-changing commands."""

    with pytest.raises(pytest.UsageError, match="dedicated stms_test service"):
        validate_migration_test_target(database_url)
