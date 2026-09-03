"""Provider-neutral request contracts and one lazy OpenAI adapter."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Never, Protocol, Self, cast

import openai
from openai import OpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

PROVIDER_CONFIGURATION_MESSAGE = "Model provider is not configured"
PROVIDER_AUTHENTICATION_MESSAGE = "Model provider authentication failed"
PROVIDER_PERMISSION_MESSAGE = "Model provider permission was denied"
PROVIDER_TIMEOUT_MESSAGE = "Model provider request timed out"
PROVIDER_TRANSIENT_MESSAGE = "Model provider is temporarily unavailable"
PROVIDER_INVALID_RESPONSE_MESSAGE = "Model provider returned an invalid response"
PROVIDER_FAILURE_MESSAGE = "Model provider request failed"


class ProviderError(Exception):
    """Base safe provider exception without SDK diagnostics or payloads."""


class ProviderConfigurationError(ProviderError):
    """Signal missing or unsupported local provider configuration."""


class ProviderAuthenticationError(ProviderError):
    """Signal provider authentication failure without revealing credentials."""


class ProviderPermissionError(ProviderError):
    """Signal provider authorization failure without revealing account details."""


class ProviderTimeoutError(ProviderError):
    """Signal a provider timeout eligible for bounded orchestration retry."""


class ProviderTransientError(ProviderError):
    """Signal a temporary provider failure eligible for bounded retry."""


class ProviderInvalidResponseError(ProviderError):
    """Signal malformed model output without carrying the raw response."""


class ProviderToolDefinition(BaseModel):
    """Describe one allowlisted custom tool with a strict JSON schema."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, object]


class ProviderRequest(BaseModel):
    """Carry only serializable, provider-neutral generation inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=20_000)
    input: str = Field(min_length=1, max_length=20_000)
    output_schema_name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$",
    )
    output_schema: dict[str, object] | None = None
    tools: tuple[ProviderToolDefinition, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_output_schema_pair(self) -> Self:
        if (self.output_schema_name is None) != (self.output_schema is None):
            raise ValueError("provider output schema name and schema must be paired")
        return self


class ProviderUsage(BaseModel):
    """Normalize optional provider token counters without fabricating data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderToolCall(BaseModel):
    """Normalize one provider function call into validated serializable values."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, object]


class ProviderResponse(BaseModel):
    """Expose only normalized text, tool calls, and usage."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    output_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    tool_calls: tuple[ProviderToolCall, ...] = ()
    usage: ProviderUsage = Field(default_factory=ProviderUsage)

    @model_validator(mode="after")
    def require_provider_output(self) -> Self:
        if self.output_text is None and not self.tool_calls:
            raise ValueError("provider response must contain text or tool calls")
        return self


class ProviderStreamChunk(BaseModel):
    """Carry one bounded text delta or one normalized final response."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    output_text_delta: str | None = Field(default=None, min_length=1, max_length=20_000)
    response: ProviderResponse | None = None

    @model_validator(mode="after")
    def require_one_stream_value(self) -> Self:
        if (self.output_text_delta is None) == (self.response is None):
            raise ValueError("provider stream chunk must contain exactly one value")
        return self


class ModelProvider(Protocol):
    """Narrow port consumed by Stage 8 orchestration and deterministic fakes."""

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse: ...


class StreamingModelProvider(Protocol):
    """Optional streaming port kept separate from the basic provider protocol."""

    def stream(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> AbstractContextManager[Iterator[ProviderStreamChunk]]: ...


class _ResponsesPort(Protocol):
    def create(self, **kwargs: object) -> object: ...

    def stream(self, **kwargs: object) -> AbstractContextManager[object]: ...


class _RawResponseStream(Protocol):
    def __iter__(self) -> Iterator[object]: ...

    def get_final_response(self) -> object: ...


class _OpenAIClientPort(Protocol):
    responses: _ResponsesPort

    def with_options(
        self,
        *,
        timeout: float,
        max_retries: int,
    ) -> _OpenAIClientPort: ...


class OpenAIClientFactory(Protocol):
    def __call__(self, *, api_key: str, max_retries: int) -> object: ...


def _default_openai_client_factory(*, api_key: str, max_retries: int) -> object:
    return OpenAI(api_key=api_key, max_retries=max_retries)


def _tool_parameters(tool: ProviderToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
        "strict": True,
    }


def _normalize_usage(response: object) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ProviderUsage()
    return ProviderUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def _provider_parameters(request: ProviderRequest) -> dict[str, object]:
    parameters: dict[str, object] = {
        "model": request.model,
        "instructions": request.instructions,
        "input": request.input,
        "parallel_tool_calls": False,
        "store": False,
    }
    if request.output_schema_name is not None and request.output_schema is not None:
        parameters["text"] = {
            "format": {
                "type": "json_schema",
                "name": request.output_schema_name,
                "schema": request.output_schema,
                "strict": True,
            }
        }
    if request.tools:
        parameters["tools"] = [_tool_parameters(tool) for tool in request.tools]
    return parameters


def _normalize_tool_calls(response: object) -> tuple[ProviderToolCall, ...]:
    calls: list[ProviderToolCall] = []
    for item in getattr(response, "output", ()):
        if getattr(item, "type", None) != "function_call":
            continue
        raw_arguments = getattr(item, "arguments", "")
        try:
            arguments = json.loads(raw_arguments)
        except TypeError, json.JSONDecodeError:
            raise ProviderInvalidResponseError(
                PROVIDER_INVALID_RESPONSE_MESSAGE
            ) from None
        if not isinstance(arguments, dict):
            raise ProviderInvalidResponseError(PROVIDER_INVALID_RESPONSE_MESSAGE)
        calls.append(
            ProviderToolCall(
                call_id=getattr(item, "call_id", ""),
                name=getattr(item, "name", ""),
                arguments=arguments,
            )
        )
    return tuple(calls)


def _normalize_response(response: object) -> ProviderResponse:
    raw_output_text = getattr(response, "output_text", None)
    if raw_output_text is not None and not isinstance(raw_output_text, str):
        raise ProviderInvalidResponseError(PROVIDER_INVALID_RESPONSE_MESSAGE)
    output_text = (
        raw_output_text if raw_output_text and raw_output_text.strip() else None
    )
    try:
        tool_calls = _normalize_tool_calls(response)
        if output_text is None and not tool_calls:
            raise ProviderInvalidResponseError(PROVIDER_INVALID_RESPONSE_MESSAGE)
        return ProviderResponse(
            output_text=output_text,
            tool_calls=tool_calls,
            usage=_normalize_usage(response),
        )
    except ProviderInvalidResponseError:
        raise
    except TypeError, ValueError, ValidationError:
        raise ProviderInvalidResponseError(PROVIDER_INVALID_RESPONSE_MESSAGE) from None


def _raise_normalized_provider_error(error: Exception) -> Never:
    if isinstance(error, ProviderError):
        raise error
    if isinstance(error, openai.APITimeoutError):
        raise ProviderTimeoutError(PROVIDER_TIMEOUT_MESSAGE) from None
    if isinstance(error, openai.AuthenticationError):
        raise ProviderAuthenticationError(PROVIDER_AUTHENTICATION_MESSAGE) from None
    if isinstance(error, openai.PermissionDeniedError):
        raise ProviderPermissionError(PROVIDER_PERMISSION_MESSAGE) from None
    if isinstance(error, (openai.APIConnectionError, openai.RateLimitError)):
        raise ProviderTransientError(PROVIDER_TRANSIENT_MESSAGE) from None
    if isinstance(error, openai.APIStatusError) and (
        error.status_code in {408, 409, 429} or error.status_code >= 500
    ):
        raise ProviderTransientError(PROVIDER_TRANSIENT_MESSAGE) from None
    raise ProviderError(PROVIDER_FAILURE_MESSAGE) from None


class OpenAIProvider:
    """Translate provider-neutral values to the synchronous OpenAI Responses API."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        client_factory: OpenAIClientFactory = _default_openai_client_factory,
    ) -> None:
        self._api_key = api_key
        self._client_factory = client_factory
        self._client: _OpenAIClientPort | None = None

    def _get_client(self) -> _OpenAIClientPort:
        if self._api_key is None:
            raise ProviderConfigurationError(PROVIDER_CONFIGURATION_MESSAGE)
        if self._client is None:
            raw_client = self._client_factory(
                api_key=self._api_key.get_secret_value(),
                max_retries=0,
            )
            self._client = cast(_OpenAIClientPort, raw_client)
        return self._client

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        """Perform one request with SDK retries disabled for caller-owned bounds."""

        if timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        client = self._get_client().with_options(
            timeout=timeout_seconds,
            max_retries=0,
        )
        parameters = _provider_parameters(request)

        try:
            response = client.responses.create(**parameters)
            return _normalize_response(response)
        except Exception as error:
            _raise_normalized_provider_error(error)

    @contextmanager
    def stream(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> Iterator[Iterator[ProviderStreamChunk]]:
        """Normalize the official synchronous Responses streaming context."""

        if timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        client = self._get_client().with_options(
            timeout=timeout_seconds,
            max_retries=0,
        )
        try:
            stream_manager = client.responses.stream(**_provider_parameters(request))
            raw_stream = cast(_RawResponseStream, stream_manager.__enter__())
        except Exception as error:
            _raise_normalized_provider_error(error)

        def chunks() -> Iterator[ProviderStreamChunk]:
            try:
                for event in raw_stream:
                    if getattr(event, "type", None) != "response.output_text.delta":
                        continue
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        yield ProviderStreamChunk(output_text_delta=delta)
                final_response = raw_stream.get_final_response()
                yield ProviderStreamChunk(response=_normalize_response(final_response))
            except Exception as error:
                _raise_normalized_provider_error(error)

        try:
            yield chunks()
        finally:
            stream_manager.__exit__(None, None, None)
