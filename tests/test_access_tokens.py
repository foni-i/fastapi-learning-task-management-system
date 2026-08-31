"""Connection-free tests for short-lived access JWT primitives."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import Settings
from app.core.tokens import (
    ACCESS_TOKEN_ALGORITHM,
    ACCESS_TOKEN_CONFIGURATION_ERROR_MESSAGE,
    ACCESS_TOKEN_ERROR_MESSAGE,
    ACCESS_TOKEN_TYPE,
    REQUIRED_ACCESS_TOKEN_CLAIMS,
    AccessTokenConfigurationError,
    AccessTokenError,
    create_access_token,
    validate_access_token,
)

TEST_SECRET = "synthetic-access-token-secret-for-unit-tests-only-64-characters-long"
TEST_ISSUER = "test-issuer"
TEST_AUDIENCE = "test-audience"


def make_settings(**overrides: object) -> Settings:
    """Build explicit test-only token settings without environment state."""

    values: dict[str, object] = {
        "access_token_secret": TEST_SECRET,
        "access_token_ttl_minutes": 15,
        "access_token_issuer": TEST_ISSUER,
        "access_token_audience": TEST_AUDIENCE,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def make_payload(**overrides: object) -> dict[str, object]:
    """Create complete controlled claims for invalid-token cases."""

    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(uuid4()),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
    }
    payload.update(overrides)
    return payload


def encode_payload(
    payload: dict[str, object],
    *,
    secret: str = TEST_SECRET,
    algorithm: str = ACCESS_TOKEN_ALGORITHM,
) -> str:
    """Encode controlled claims without printing the complete token."""

    return jwt.encode(payload, secret, algorithm=algorithm)


def test_access_token_round_trips_required_claims_and_utc_lifetime() -> None:
    """Issue a signed token and expose only validated typed claims."""

    settings = make_settings()
    user_id = uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)

    token = create_access_token(user_id, settings=settings, issued_at=issued_at)
    unverified = jwt.decode(token, options={"verify_signature": False})
    claims = validate_access_token(token, settings=settings)

    assert set(unverified) == set(REQUIRED_ACCESS_TOKEN_CLAIMS)
    assert unverified["sub"] == str(user_id)
    assert unverified["type"] == ACCESS_TOKEN_TYPE
    assert unverified["iss"] == TEST_ISSUER
    assert unverified["aud"] == TEST_AUDIENCE
    assert claims.subject == user_id
    assert claims.issued_at == issued_at
    assert claims.expires_at == issued_at + timedelta(minutes=15)


@pytest.mark.parametrize(
    ("payload_change", "secret", "algorithm"),
    [
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, TEST_SECRET, "HS256"),
        ({"type": "refresh"}, TEST_SECRET, "HS256"),
        ({"iss": "wrong-issuer"}, TEST_SECRET, "HS256"),
        ({"aud": "wrong-audience"}, TEST_SECRET, "HS256"),
        ({}, "different-synthetic-secret-value", "HS256"),
        ({}, TEST_SECRET, "HS384"),
    ],
    ids=(
        "expired",
        "wrong-type",
        "wrong-issuer",
        "wrong-audience",
        "tampered",
        "wrong-algorithm",
    ),
)
def test_access_token_rejects_invalid_security_contracts(
    payload_change: dict[str, object],
    secret: str,
    algorithm: str,
) -> None:
    """Reject invalid tokens through one fixed non-sensitive error."""

    token = encode_payload(
        make_payload(**payload_change),
        secret=secret,
        algorithm=algorithm,
    )

    with pytest.raises(AccessTokenError) as exc_info:
        validate_access_token(token, settings=make_settings())

    assert str(exc_info.value) == ACCESS_TOKEN_ERROR_MESSAGE
    assert token not in str(exc_info.value)
    assert TEST_SECRET not in str(exc_info.value)


@pytest.mark.parametrize("missing_claim", REQUIRED_ACCESS_TOKEN_CLAIMS)
def test_access_token_requires_every_planned_claim(missing_claim: str) -> None:
    """Refuse tokens missing any identity, type, time, issuer, or audience claim."""

    payload = make_payload()
    del payload[missing_claim]
    token = encode_payload(payload)

    with pytest.raises(AccessTokenError, match=f"^{ACCESS_TOKEN_ERROR_MESSAGE}$"):
        validate_access_token(token, settings=make_settings())


@pytest.mark.parametrize("token", ["", "not-a-jwt", "one.two.three"])
def test_access_token_rejects_malformed_input_without_echoing_it(token: str) -> None:
    """Keep malformed credential material out of diagnostics."""

    with pytest.raises(AccessTokenError) as exc_info:
        validate_access_token(token, settings=make_settings())

    assert str(exc_info.value) == ACCESS_TOKEN_ERROR_MESSAGE
    if token:
        assert token not in str(exc_info.value)


def test_access_token_requires_configured_secret_for_create_and_validate() -> None:
    """Fail safely when signing configuration is absent."""

    settings = Settings()

    with pytest.raises(AccessTokenConfigurationError) as create_error:
        create_access_token(uuid4(), settings=settings)
    with pytest.raises(AccessTokenConfigurationError) as validate_error:
        validate_access_token("controlled-token-value", settings=settings)

    assert str(create_error.value) == ACCESS_TOKEN_CONFIGURATION_ERROR_MESSAGE
    assert str(validate_error.value) == ACCESS_TOKEN_CONFIGURATION_ERROR_MESSAGE


def test_access_token_rejects_naive_issue_time() -> None:
    """Require timezone-aware creation input rather than pretending it is UTC."""

    with pytest.raises(ValueError, match="timezone-aware"):
        create_access_token(
            uuid4(),
            settings=make_settings(),
            issued_at=datetime(2026, 8, 31, 8, 30),
        )
