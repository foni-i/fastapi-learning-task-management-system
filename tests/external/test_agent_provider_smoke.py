"""Tiny opt-in OpenAI adapter smoke test; never part of ordinary CI."""

import os

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agent.providers import OpenAIProvider, ProviderRequest
from app.core.config import Settings


class SmokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(pattern=r"^ok$")


@pytest.mark.external_provider
def test_openai_provider_returns_tiny_structured_response() -> None:
    if os.getenv("STMS_RUN_EXTERNAL_PROVIDER_SMOKE") != "1":
        pytest.skip("external provider smoke test requires explicit opt-in")

    settings = Settings()
    if (
        settings.model_provider != "openai"
        or settings.model_name is None
        or settings.model_api_key is None
    ):
        pytest.skip("external provider smoke test requires explicit configuration")

    provider = OpenAIProvider(api_key=settings.model_api_key)
    response = provider.generate(
        ProviderRequest(
            model=settings.model_name,
            prompt_version="study-plan.v1",
            instructions="Return only the requested synthetic smoke-test object.",
            input="Return status ok. This is a synthetic provider smoke test.",
            output_schema_name="SmokeResult",
            output_schema=SmokeResult.model_json_schema(),
        ),
        timeout_seconds=30,
    )

    assert response.output_text is not None
    assert SmokeResult.model_validate_json(response.output_text).status == "ok"
