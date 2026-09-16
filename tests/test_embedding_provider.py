"""Offline contracts for fixed-size embeddings and the OpenAI adapter."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.agent.embeddings import (
    EMBEDDING_CONFIGURATION_MESSAGE,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_INVALID_RESPONSE_MESSAGE,
    EMBEDDING_UNAVAILABLE_MESSAGE,
    EmbeddingProviderConfigurationError,
    EmbeddingProviderInvalidResponseError,
    EmbeddingProviderUnavailableError,
    OpenAIEmbeddingProvider,
    validate_embedding_batch,
)
from tests.fakes.embedding_provider import DeterministicEmbeddingProvider


class FakeEmbeddingsResource:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.parameters: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.parameters = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeOpenAIClient:
    def __init__(self, response: object | Exception) -> None:
        self.embeddings = FakeEmbeddingsResource(response)
        self.options: dict[str, object] = {}

    def with_options(self, **kwargs: object) -> FakeOpenAIClient:
        self.options = kwargs
        return self


def _vector(value: float = 0.25) -> list[float]:
    return [value] * EMBEDDING_DIMENSIONS


def test_validate_embedding_batch_rejects_count_dimension_and_nonfinite() -> None:
    assert validate_embedding_batch([_vector()], expected_count=1)[0][0] == 0.25

    invalid_batches: tuple[tuple[list[list[float]], int], ...] = (
        ([], 1),
        ([_vector()[:-1]], 1),
        ([[*_vector()[:-1], float("nan")]], 1),
        ([[*_vector()[:-1], float("inf")]], 1),
    )
    for vectors, count in invalid_batches:
        with pytest.raises(
            EmbeddingProviderInvalidResponseError,
            match=EMBEDDING_INVALID_RESPONSE_MESSAGE,
        ):
            validate_embedding_batch(vectors, expected_count=count)


def test_openai_adapter_sends_one_bounded_request_and_restores_order() -> None:
    response = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=_vector(0.2)),
            SimpleNamespace(index=0, embedding=_vector(0.1)),
        ]
    )
    client = FakeOpenAIClient(response)
    created: dict[str, object] = {}

    def factory(**kwargs: object) -> object:
        created.update(kwargs)
        return client

    provider = OpenAIEmbeddingProvider(
        api_key=SecretStr("synthetic-secret-key"),
        model="synthetic-embedding-model",
        client_factory=factory,
    )

    vectors = provider.embed(("first", "second"), timeout_seconds=7.5)

    assert vectors[0][0] == 0.1
    assert vectors[1][0] == 0.2
    assert created == {"api_key": "synthetic-secret-key", "max_retries": 0}
    assert client.options == {"timeout": 7.5, "max_retries": 0}
    assert client.embeddings.parameters == {
        "model": "synthetic-embedding-model",
        "input": ["first", "second"],
        "dimensions": 1536,
        "encoding_format": "float",
    }


def test_adapter_rejects_missing_config_without_creating_a_client() -> None:
    calls = 0

    def factory(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return FakeOpenAIClient(SimpleNamespace(data=[]))

    provider = OpenAIEmbeddingProvider(
        api_key=None,
        model=None,
        client_factory=factory,
    )

    with pytest.raises(
        EmbeddingProviderConfigurationError,
        match=EMBEDDING_CONFIGURATION_MESSAGE,
    ):
        provider.embed(("private input",), timeout_seconds=1)
    assert calls == 0


@pytest.mark.parametrize(
    ("texts", "timeout"),
    [
        ((), 1.0),
        (("x",) * 201, 1.0),
        (("x" * 2001,), 1.0),
        (("x",), 0.0),
        (("x",), float("nan")),
    ],
)
def test_adapter_rejects_unbounded_inputs_before_creating_a_client(
    texts: tuple[str, ...],
    timeout: float,
) -> None:
    calls = 0

    def factory(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return FakeOpenAIClient(SimpleNamespace(data=[]))

    provider = OpenAIEmbeddingProvider(
        api_key=SecretStr("synthetic-secret-key"),
        model="synthetic-model",
        client_factory=factory,
    )

    with pytest.raises(ValueError):
        provider.embed(texts, timeout_seconds=timeout)
    assert calls == 0


def test_adapter_normalizes_sdk_and_invalid_response_without_sensitive_echo() -> None:
    private = "private provider payload"
    unavailable = OpenAIEmbeddingProvider(
        api_key=SecretStr("synthetic-secret-key"),
        model="synthetic-model",
        client_factory=lambda **_kwargs: FakeOpenAIClient(RuntimeError(private)),
    )
    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match=EMBEDDING_UNAVAILABLE_MESSAGE,
    ) as unavailable_info:
        unavailable.embed(("private input",), timeout_seconds=1)
    assert private not in str(unavailable_info.value)

    malformed = OpenAIEmbeddingProvider(
        api_key=SecretStr("synthetic-secret-key"),
        model="synthetic-model",
        client_factory=lambda **_kwargs: FakeOpenAIClient(
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0])])
        ),
    )
    with pytest.raises(
        EmbeddingProviderInvalidResponseError,
        match=EMBEDDING_INVALID_RESPONSE_MESSAGE,
    ):
        malformed.embed(("private input",), timeout_seconds=1)


def test_deterministic_fake_is_stable_bounded_and_does_not_record_text() -> None:
    provider = DeterministicEmbeddingProvider()

    first = provider.embed(("private alpha", "private beta"), timeout_seconds=2)
    second = provider.embed(("private alpha", "private beta"), timeout_seconds=2)

    assert first == second
    assert first[0] != first[1]
    assert len(first) == 2
    assert all(len(vector) == EMBEDDING_DIMENSIONS for vector in first)
    assert len(provider.calls) == 2
    assert provider.calls[0].input_count == 2
    assert provider.calls[0].input_lengths == (13, 12)
    assert "private alpha" not in repr(provider.calls)
