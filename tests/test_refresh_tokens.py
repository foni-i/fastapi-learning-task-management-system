"""Opaque credential entropy, exact hashing and non-disclosure contracts."""

import hashlib
import re
import secrets
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from app.core import refresh_tokens


def test_generation_requests_exact_random_byte_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = Mock(return_value="a" * 43)
    monkeypatch.setattr(secrets, "token_urlsafe", generator)
    token = refresh_tokens.generate_refresh_token()
    generator.assert_called_once_with(32)
    assert isinstance(token, SecretStr)
    assert token.get_secret_value() not in repr(token)
    assert token.get_secret_value() not in str(token)


def test_real_generation_is_urlsafe_bounded_and_distinct() -> None:
    first = refresh_tokens.generate_refresh_token()
    second = refresh_tokens.generate_refresh_token()
    valid = re.fullmatch(r"[A-Za-z0-9_-]{43}", first.get_secret_value()) is not None
    distinct = first != second
    assert valid
    assert distinct


def test_digest_uses_exact_bytes_and_lowercase_sha256() -> None:
    raw = "aB0_-" * 8 + "xyz"
    digest = refresh_tokens.hash_refresh_token(SecretStr(raw))
    matches = digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    changed_case_differs = digest != refresh_tokens.hash_refresh_token(
        SecretStr(raw.swapcase())
    )
    assert matches
    assert changed_case_differs
    assert re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    assert raw not in digest


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "a" * 42,
        "a" * 44,
        " " + "a" * 43,
        "a" * 43 + "\n",
        "界" * 43,
        "a" * 42 + ".",
    ],
    ids=["empty", "short", "long", "space", "newline", "unicode", "punctuation"],
)
def test_hash_rejects_invalid_input_without_echoing_it(raw: str) -> None:
    with pytest.raises(ValueError) as error:
        refresh_tokens.hash_refresh_token(SecretStr(raw))
    assert str(error.value) == refresh_tokens.INVALID_REFRESH_TOKEN_MESSAGE
