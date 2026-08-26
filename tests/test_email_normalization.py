"""Unit tests for deterministic, network-free email normalization."""

from types import SimpleNamespace

import pytest

from app.core import email_normalization
from app.core.email_normalization import (
    INVALID_EMAIL_MESSAGE,
    EmailNormalizationError,
    normalize_email,
)


@pytest.mark.parametrize(
    ("raw_email", "expected"),
    [
        ("  User@Example.COM\t", "user@example.com"),
        ("Mixed.Case@EXAMPLE.COM", "mixed.case@example.com"),
        ("Straße@Example.com", "strasse@example.com"),
        ("User@例子.测试", "user@例子.测试"),
        ("User@xn--fsqu00a.xn--0zwm56d", "user@例子.测试"),
    ],
)
def test_normalize_email_returns_one_canonical_value(
    raw_email: str,
    expected: str,
) -> None:
    """Trim, validate, normalize the domain, then casefold the full address."""

    assert normalize_email(raw_email) == expected


@pytest.mark.parametrize(
    "raw_email",
    [
        "",
        "   \t\r\n",
        "@example.com",
        "user@",
        "user.example.com",
        "user@@example.com",
        "user@invalid_domain",
    ],
)
def test_normalize_email_rejects_invalid_input_with_fixed_safe_error(
    raw_email: str,
) -> None:
    """Every invalid shape receives one error that never echoes raw input."""

    with pytest.raises(EmailNormalizationError) as exc_info:
        normalize_email(raw_email)

    assert str(exc_info.value) == INVALID_EMAIL_MESSAGE
    if raw_email:
        assert raw_email not in str(exc_info.value)


def test_normalize_email_rejects_an_overlong_address_safely() -> None:
    """Reject a syntactically structured email whose total length exceeds 254."""

    domain = ".".join(("b" * 63, "c" * 63, "d" * 62))
    raw_email = f"{'a' * 64}@{domain}"

    assert len(raw_email) == 255
    with pytest.raises(EmailNormalizationError, match=f"^{INVALID_EMAIL_MESSAGE}$"):
        normalize_email(raw_email)


def test_normalize_email_rechecks_normalized_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the storage boundary even if validator behavior changes later."""

    monkeypatch.setattr(
        email_normalization,
        "validate_email",
        lambda *_args, **_kwargs: SimpleNamespace(normalized="a" * 255),
    )

    with pytest.raises(EmailNormalizationError, match=f"^{INVALID_EMAIL_MESSAGE}$"):
        normalize_email("syntactically-valid@example.com")


def test_semantically_equivalent_inputs_normalize_identically() -> None:
    """Whitespace, case, and IDN representation do not create new identities."""

    variants = (
        "  User@xn--fsqu00a.xn--0zwm56d ",
        "user@例子.测试",
        "USER@例子.测试",
    )

    assert {normalize_email(value) for value in variants} == {"user@例子.测试"}


@pytest.mark.parametrize(
    "raw_email",
    [
        " User@EXAMPLE.COM ",
        "Straße@Example.com",
        "User@xn--fsqu00a.xn--0zwm56d",
    ],
)
def test_normalize_email_is_idempotent(raw_email: str) -> None:
    """Normalizing a canonical value again leaves it unchanged."""

    normalized = normalize_email(raw_email)

    assert normalize_email(normalized) == normalized
