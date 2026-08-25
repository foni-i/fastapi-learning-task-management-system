"""Connection-free tests for integration database safety guards."""

import pytest

from tests.integration.conftest import validate_test_database_url

SAFE_TEST_URL = "postgresql+psycopg://stms_test:test-password@127.0.0.1:5433/stms_test"
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
