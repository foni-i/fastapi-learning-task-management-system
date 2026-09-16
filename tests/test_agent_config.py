"""Secret-aware configuration tests for the Stage 8 model provider."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def isolate_model_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "STMS_MODEL_PROVIDER",
        "STMS_MODEL_NAME",
        "STMS_EMBEDDING_MODEL",
        "STMS_EMBEDDING_TIMEOUT_SECONDS",
        "STMS_MODEL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_model_settings_are_optional_for_ordinary_application_imports() -> None:
    settings = Settings()

    assert settings.model_provider is None
    assert settings.model_name is None
    assert settings.embedding_model is None
    assert settings.embedding_timeout_seconds == 30.0
    assert settings.model_api_key is None


def test_model_settings_load_and_mask_the_synthetic_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "synthetic-provider-key-for-tests"
    monkeypatch.setenv("STMS_MODEL_PROVIDER", " openai ")
    monkeypatch.setenv("STMS_MODEL_NAME", " synthetic-model ")
    monkeypatch.setenv("STMS_EMBEDDING_MODEL", " synthetic-embedding-model ")
    monkeypatch.setenv("STMS_EMBEDDING_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("STMS_MODEL_API_KEY", key)

    settings = Settings()

    assert settings.model_provider == "openai"
    assert settings.model_name == "synthetic-model"
    assert settings.embedding_model == "synthetic-embedding-model"
    assert settings.embedding_timeout_seconds == 12.5
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == key
    assert key not in repr(settings)
    assert settings.model_dump(mode="json")["model_api_key"] == "**********"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_provider", "   ", "model provider must not be blank"),
        ("model_provider", "p" * 101, "model provider is too long"),
        ("model_name", "   ", "model name must not be blank"),
        ("model_name", "m" * 201, "model name is too long"),
        ("embedding_model", "   ", "model name must not be blank"),
        ("embedding_model", "m" * 201, "model name is too long"),
    ],
)
def test_model_identity_rejects_invalid_bounds(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings.model_validate({field: value})


def test_blank_model_key_is_rejected_without_echoing_input() -> None:
    rejected_key = "   "

    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate({"model_api_key": rejected_key})

    assert "model API key must not be blank" in str(exc_info.value)
    assert repr(rejected_key) not in str(exc_info.value)


@pytest.mark.parametrize("timeout", [0, 0.09, 120.1])
def test_embedding_timeout_rejects_unbounded_values(timeout: float) -> None:
    with pytest.raises(ValidationError, match=r"between 0\.1 and 120 seconds"):
        Settings.model_validate({"embedding_timeout_seconds": timeout})
