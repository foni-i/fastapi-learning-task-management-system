"""Unit tests for password policy and Argon2id primitives."""

import pytest
from pydantic import ValidationError

from app.core.security import (
    PASSWORD_POLICY_ERROR_MESSAGE,
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from app.schemas.user import UserRegistrationRequest


@pytest.mark.parametrize("length", [11, 129])
def test_password_policy_rejects_out_of_range_lengths(length: int) -> None:
    """Reject values immediately outside the inclusive 12-128 boundary."""

    with pytest.raises(
        PasswordPolicyError,
        match=f"^{PASSWORD_POLICY_ERROR_MESSAGE}$",
    ):
        validate_password("x" * length)


@pytest.mark.parametrize("password", ["x" * 12, "x" * 128, "密" * 12])
def test_password_policy_accepts_unicode_character_boundaries(password: str) -> None:
    """Count Python Unicode characters and preserve accepted content exactly."""

    assert validate_password(password) == password


@pytest.mark.parametrize("password", [" " * 12, "\t" * 12, " \t\n\r " * 3])
def test_password_policy_rejects_all_whitespace(password: str) -> None:
    """Reject empty-information passwords without adding composition rules."""

    with pytest.raises(PasswordPolicyError) as exc_info:
        validate_password(password)

    assert str(exc_info.value) == PASSWORD_POLICY_ERROR_MESSAGE
    assert password not in str(exc_info.value)


def test_registration_schema_preserves_and_redacts_an_accepted_password() -> None:
    """Keep meaningful spaces and casing behind the existing SecretStr boundary."""

    plaintext = "  Mixed CASE 密码  "
    request = UserRegistrationRequest.model_validate(
        {"email": "user@example.com", "password": plaintext}
    )

    assert request.password.get_secret_value() == plaintext
    assert plaintext not in repr(request)
    assert plaintext not in str(request)
    assert plaintext not in request.model_dump_json()


@pytest.mark.parametrize("password", ["x" * 11, "x" * 129])
def test_registration_schema_reuses_the_shared_password_policy(password: str) -> None:
    """Apply centralized bounds without disclosing rejected input."""

    with pytest.raises(ValidationError) as exc_info:
        UserRegistrationRequest.model_validate(
            {"email": "user@example.com", "password": password}
        )

    error_text = str(exc_info.value)
    assert PASSWORD_POLICY_ERROR_MESSAGE in error_text
    assert password not in error_text


def test_argon2id_hash_verification_and_salt_nondeterminism() -> None:
    """Use identifiable Argon2id hashes and a fresh salt for every call."""

    password = "correct horse battery"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash.startswith("$argon2id$")
    assert second_hash.startswith("$argon2id$")
    assert first_hash != second_hash
    assert verify_password(password, first_hash) is True
    assert verify_password("incorrect horse battery", first_hash) is False


@pytest.mark.parametrize("malformed_hash", ["", "not-a-password-hash", "$argon2id$"])
def test_malformed_hash_is_rejected_without_diagnostics(malformed_hash: str) -> None:
    """Return only a boolean when stored hash material is malformed."""

    assert verify_password("correct horse battery", malformed_hash) is False
