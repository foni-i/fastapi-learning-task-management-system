"""Offline contract tests for the provider port and OpenAI adapter."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.agent.providers import (
    PROVIDER_CONFIGURATION_MESSAGE,
    PROVIDER_FAILURE_MESSAGE,
    PROVIDER_INVALID_RESPONSE_MESSAGE,
    ModelProvider,
    OpenAIProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRequest,
    ProviderResponse,
)


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="synthetic-model",
        prompt_version="study-plan.v1",
        instructions="Return a strict test result.",
        input="Untrusted synthetic input.",
        output_schema_name="SyntheticResult",
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )


class FakeResponses:
    def __init__(self, result: object | Exception) -> None:
        self.result = result
        self.parameters: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.parameters = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRawStream:
    def __init__(self, events: list[object], final_response: object) -> None:
        self.events = events
        self.final_response = final_response
        self.closed = False

    def __iter__(self) -> object:
        return iter(self.events)

    def get_final_response(self) -> object:
        return self.final_response

    def close(self) -> None:
        self.closed = True


class FakeStreamManager:
    def __init__(self, stream: FakeRawStream) -> None:
        self.stream = stream

    def __enter__(self) -> FakeRawStream:
        return self.stream

    def __exit__(self, *args: object) -> None:
        self.stream.close()


class FakeStreamingResponses(FakeResponses):
    def __init__(self, stream: FakeRawStream) -> None:
        super().__init__(stream.final_response)
        self.stream_value = stream
        self.stream_parameters: dict[str, object] | None = None

    def stream(self, **kwargs: object) -> FakeStreamManager:
        self.stream_parameters = kwargs
        return FakeStreamManager(self.stream_value)


class FakeClient:
    def __init__(self, result: object | Exception) -> None:
        self.responses = FakeResponses(result)
        self.options: tuple[float, int] | None = None

    def with_options(self, *, timeout: float, max_retries: int) -> FakeClient:
        self.options = (timeout, max_retries)
        return self


def test_stream_adapter_normalizes_chunks_final_response_and_closes() -> None:
    final = SimpleNamespace(output_text='{"ok":true}', output=[], usage=None)
    raw_stream = FakeRawStream(
        [
            SimpleNamespace(type="response.output_text.delta", delta='{"ok":'),
            SimpleNamespace(type="response.output_text.delta", delta="true}"),
            SimpleNamespace(type="response.other"),
        ],
        final,
    )
    responses = FakeStreamingResponses(raw_stream)
    client = FakeClient(final)
    client.responses = responses
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(client),
    )

    with provider.stream(_request(), timeout_seconds=10.0) as stream:
        chunks = list(stream)

    assert [chunk.output_text_delta for chunk in chunks[:-1]] == [
        '{"ok":',
        "true}",
    ]
    assert chunks[-1].response is not None
    assert chunks[-1].response.output_text == '{"ok":true}'
    assert raw_stream.closed is True
    assert responses.stream_parameters is not None
    assert responses.stream_parameters["store"] is False


def test_stream_adapter_closes_when_consumer_raises() -> None:
    final = SimpleNamespace(output_text='{"ok":true}', output=[], usage=None)
    raw_stream = FakeRawStream(
        [SimpleNamespace(type="response.output_text.delta", delta="x")],
        final,
    )
    client = FakeClient(final)
    client.responses = FakeStreamingResponses(raw_stream)
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(client),
    )

    with (
        pytest.raises(RuntimeError, match="cancelled"),
        provider.stream(_request(), timeout_seconds=10.0) as stream,
    ):
        next(stream)
        raise RuntimeError("cancelled")

    assert raw_stream.closed is True


class RecordingFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls: list[tuple[str, int]] = []

    def __call__(self, *, api_key: str, max_retries: int) -> object:
        self.calls.append((api_key, max_retries))
        return self.client


def test_adapter_is_lazy_and_disables_sdk_retries() -> None:
    response = SimpleNamespace(
        output_text='{"ok":true}',
        output=[],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
    )
    client = FakeClient(response)
    factory = RecordingFactory(client)
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=factory,
    )

    assert factory.calls == []

    result = provider.generate(_request(), timeout_seconds=30.0)

    assert factory.calls == [("synthetic-provider-key", 0)]
    assert client.options == (30.0, 0)
    assert result.output_text == '{"ok":true}'
    assert result.usage.model_dump() == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
    }
    assert client.responses.parameters is not None
    assert client.responses.parameters["store"] is False
    assert client.responses.parameters["parallel_tool_calls"] is False


def test_adapter_normalizes_function_calls_without_sdk_types() -> None:
    response = SimpleNamespace(
        output_text='{"ok":true}',
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="list_tasks",
                arguments='{"page":1}',
            )
        ],
        usage=None,
    )
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(FakeClient(response)),
    )

    result = provider.generate(_request(), timeout_seconds=10.0)

    assert result.tool_calls[0].model_dump() == {
        "call_id": "call_1",
        "name": "list_tasks",
        "arguments": {"page": 1},
    }
    assert result.usage.model_dump() == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_adapter_accepts_a_tool_call_without_text_output() -> None:
    response = SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="list_tasks",
                arguments='{"page":1}',
            )
        ],
        usage=None,
    )
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(FakeClient(response)),
    )

    result = provider.generate(_request(), timeout_seconds=10.0)

    assert result.output_text is None
    assert result.tool_calls[0].name == "list_tasks"


def test_invalid_tool_call_fields_are_redacted() -> None:
    raw_name = "INVALID RAW TOOL NAME"
    response = SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name=raw_name,
                arguments="{}",
            )
        ],
        usage=None,
    )
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(FakeClient(response)),
    )

    with pytest.raises(
        ProviderInvalidResponseError,
        match=PROVIDER_INVALID_RESPONSE_MESSAGE,
    ) as exc_info:
        provider.generate(_request(), timeout_seconds=10.0)

    assert raw_name not in str(exc_info.value)


def test_missing_key_fails_only_when_adapter_is_used() -> None:
    provider = OpenAIProvider(api_key=None)

    with pytest.raises(
        ProviderConfigurationError,
        match=PROVIDER_CONFIGURATION_MESSAGE,
    ):
        provider.generate(_request(), timeout_seconds=30.0)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(output_text="", output=[], usage=None),
        SimpleNamespace(
            output_text='{"ok":true}',
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="list_tasks",
                    arguments="not-json",
                )
            ],
            usage=None,
        ),
    ],
)
def test_invalid_sdk_response_is_rejected_without_raw_output(response: object) -> None:
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(FakeClient(response)),
    )

    with pytest.raises(
        ProviderInvalidResponseError,
        match=PROVIDER_INVALID_RESPONSE_MESSAGE,
    ) as exc_info:
        provider.generate(_request(), timeout_seconds=30.0)

    assert "not-json" not in str(exc_info.value)


def test_unknown_sdk_error_is_redacted() -> None:
    sensitive_diagnostic = "synthetic-key appeared in transport diagnostic"
    provider = OpenAIProvider(
        api_key=SecretStr("synthetic-provider-key"),
        client_factory=RecordingFactory(FakeClient(RuntimeError(sensitive_diagnostic))),
    )

    with pytest.raises(ProviderError, match=PROVIDER_FAILURE_MESSAGE) as exc_info:
        provider.generate(_request(), timeout_seconds=30.0)

    assert sensitive_diagnostic not in str(exc_info.value)


def test_deterministic_fake_can_satisfy_provider_protocol() -> None:
    class DeterministicProvider:
        def generate(
            self,
            request: ProviderRequest,
            *,
            timeout_seconds: float,
        ) -> ProviderResponse:
            assert timeout_seconds == 30.0
            return ProviderResponse(output_text='{"ok":true}')

    provider: ModelProvider = DeterministicProvider()

    assert provider.generate(_request(), timeout_seconds=30.0).output_text
