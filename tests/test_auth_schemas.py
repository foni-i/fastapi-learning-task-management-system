"""Unit tests for login input and access-token output contracts."""

import pytest
from pydantic import SecretStr, ValidationError

from app.schemas.auth import AccessTokenResponse, UserLoginRequest

CONTROLLED_PASSWORD = "  Mixed Case Login Secret  "


def test_login_request_normalizes_email_and_preserves_secret() -> None:
    """Canonicalize only the identifier while preserving password bytes as text."""

    request = UserLoginRequest.model_validate(
        {"email": "  User@EXAMPLE.COM  ", "password": CONTROLLED_PASSWORD}
    )

    assert request.email == "user@example.com"
    assert isinstance(request.password, SecretStr)
    assert request.password.get_secret_value() == CONTROLLED_PASSWORD
    assert CONTROLLED_PASSWORD not in repr(request)
    assert CONTROLLED_PASSWORD not in str(request)


@pytest.mark.parametrize("password", ["x", "x" * 128], ids=("one", "maximum"))
def test_login_password_accepts_roadmap_boundaries(password: str) -> None:
    """Login accepts nonempty credentials without reapplying registration minimum."""

    request = UserLoginRequest.model_validate(
        {"email": "user@example.com", "password": password}
    )

    assert request.password.get_secret_value() == password


@pytest.mark.parametrize("password", ["", "x" * 129], ids=("empty", "too-long"))
def test_login_password_rejects_invalid_bounds_without_echo(password: str) -> None:
    """Reject unsafe work bounds through a fixed secret-safe validation error."""

    with pytest.raises(ValidationError) as exc_info:
        UserLoginRequest.model_validate(
            {"email": "user@example.com", "password": password}
        )

    assert password not in str(exc_info.value) if password else True
    assert "input_value" not in str(exc_info.value)


def test_login_request_rejects_extra_fields_without_secret_disclosure() -> None:
    """Keep the request allowlist strict and hide the rejected input mapping."""

    with pytest.raises(ValidationError) as exc_info:
        UserLoginRequest.model_validate(
            {
                "email": "user@example.com",
                "password": CONTROLLED_PASSWORD,
                "remember_me": True,
            }
        )

    assert CONTROLLED_PASSWORD not in str(exc_info.value)


def test_access_token_response_is_an_explicit_secret_aware_allowlist() -> None:
    """Return exactly the roadmap token fields without repr disclosure."""

    token = "controlled.compact.token"
    response = AccessTokenResponse(access_token=token)

    assert response.model_dump() == {"access_token": token, "token_type": "bearer"}
    assert set(AccessTokenResponse.model_fields) == {"access_token", "token_type"}
    assert token not in repr(response)


def test_access_token_response_rejects_extra_fields() -> None:
    """Prevent user or internal authentication data from entering the response."""

    with pytest.raises(ValidationError):
        AccessTokenResponse.model_validate(
            {
                "access_token": "controlled.compact.token",
                "token_type": "bearer",
                "password_hash": "internal-hash",
            }
        )
