"""Provider-neutral embedding port and one lazy synchronous OpenAI adapter."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from typing import Never, Protocol, cast

import openai
from openai import OpenAI
from pydantic import SecretStr

EMBEDDING_DIMENSIONS = 1_536
MAX_EMBEDDING_INPUTS = 200
MAX_EMBEDDING_INPUT_CHARACTERS = 2_000
EMBEDDING_CONFIGURATION_MESSAGE = "Embedding provider is not configured"
EMBEDDING_UNAVAILABLE_MESSAGE = "Embedding provider is temporarily unavailable"
EMBEDDING_INVALID_RESPONSE_MESSAGE = "Embedding provider returned an invalid response"

EmbeddingVector = tuple[float, ...]
EmbeddingBatch = tuple[EmbeddingVector, ...]


class EmbeddingProviderError(Exception):
    """Base safe embedding failure without provider payloads or credentials."""


class EmbeddingProviderConfigurationError(EmbeddingProviderError):
    """Signal missing provider configuration with a fixed safe message."""


class EmbeddingProviderUnavailableError(EmbeddingProviderError):
    """Normalize SDK, transport, authentication, and provider failures."""


class EmbeddingProviderInvalidResponseError(EmbeddingProviderError):
    """Reject malformed vectors without retaining the provider response."""


class EmbeddingProvider(Protocol):
    """Narrow synchronous port consumed by document indexing."""

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> Sequence[Sequence[float]]: ...


class _EmbeddingsPort(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _OpenAIEmbeddingClientPort(Protocol):
    embeddings: _EmbeddingsPort

    def with_options(
        self,
        *,
        timeout: float,
        max_retries: int,
    ) -> _OpenAIEmbeddingClientPort: ...


class OpenAIEmbeddingClientFactory(Protocol):
    def __call__(self, *, api_key: str, max_retries: int) -> object: ...


def _default_openai_client_factory(*, api_key: str, max_retries: int) -> object:
    return OpenAI(api_key=api_key, max_retries=max_retries)


def validate_embedding_batch(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
) -> EmbeddingBatch:
    """Return immutable finite 1,536-dimensional vectors or fail closed."""

    if len(vectors) != expected_count:
        raise EmbeddingProviderInvalidResponseError(EMBEDDING_INVALID_RESPONSE_MESSAGE)

    normalized: list[EmbeddingVector] = []
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingProviderInvalidResponseError(
                EMBEDDING_INVALID_RESPONSE_MESSAGE
            )
        values: list[float] = []
        for value in cast(Sequence[object], vector):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise EmbeddingProviderInvalidResponseError(
                    EMBEDDING_INVALID_RESPONSE_MESSAGE
                )
            try:
                number = float(value)
            except OverflowError:
                raise EmbeddingProviderInvalidResponseError(
                    EMBEDDING_INVALID_RESPONSE_MESSAGE
                ) from None
            if not isfinite(number):
                raise EmbeddingProviderInvalidResponseError(
                    EMBEDDING_INVALID_RESPONSE_MESSAGE
                )
            values.append(number)
        normalized.append(tuple(values))
    return tuple(normalized)


def _validate_inputs(texts: Sequence[str], timeout_seconds: float) -> tuple[str, ...]:
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("embedding timeout must be positive")
    if isinstance(texts, str):
        raise ValueError("embedding inputs must be a sequence of strings")
    inputs = tuple(texts)
    if not 1 <= len(inputs) <= MAX_EMBEDDING_INPUTS:
        raise ValueError("embedding batch must contain between 1 and 200 inputs")
    if any(not text or len(text) > MAX_EMBEDDING_INPUT_CHARACTERS for text in inputs):
        raise ValueError("embedding input must contain between 1 and 2000 characters")
    return inputs


def _raise_normalized_error(error: Exception) -> Never:
    if isinstance(error, EmbeddingProviderError):
        raise error
    if isinstance(
        error,
        (
            openai.APIConnectionError,
            openai.APIStatusError,
            openai.APITimeoutError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.RateLimitError,
        ),
    ):
        raise EmbeddingProviderUnavailableError(EMBEDDING_UNAVAILABLE_MESSAGE) from None
    raise EmbeddingProviderUnavailableError(EMBEDDING_UNAVAILABLE_MESSAGE) from None


def _normalize_response(response: object, *, expected_count: int) -> EmbeddingBatch:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingProviderInvalidResponseError(EMBEDDING_INVALID_RESPONSE_MESSAGE)

    indexed_vectors: list[tuple[int, Sequence[float]]] = []
    for item in data:
        index = getattr(item, "index", None)
        vector = getattr(item, "embedding", None)
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(vector, Sequence)
        ):
            raise EmbeddingProviderInvalidResponseError(
                EMBEDDING_INVALID_RESPONSE_MESSAGE
            )
        indexed_vectors.append((index, cast(Sequence[float], vector)))
    indexed_vectors.sort(key=lambda item: item[0])
    if [item[0] for item in indexed_vectors] != list(range(expected_count)):
        raise EmbeddingProviderInvalidResponseError(EMBEDDING_INVALID_RESPONSE_MESSAGE)
    return validate_embedding_batch(
        [item[1] for item in indexed_vectors],
        expected_count=expected_count,
    )


class OpenAIEmbeddingProvider:
    """Translate the embedding port to the synchronous OpenAI SDK."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        model: str | None,
        client_factory: OpenAIEmbeddingClientFactory = _default_openai_client_factory,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client_factory = client_factory
        self._client: _OpenAIEmbeddingClientPort | None = None

    def _get_client(self) -> _OpenAIEmbeddingClientPort:
        if self._api_key is None or self._model is None:
            raise EmbeddingProviderConfigurationError(EMBEDDING_CONFIGURATION_MESSAGE)
        if self._client is None:
            client = self._client_factory(
                api_key=self._api_key.get_secret_value(),
                max_retries=0,
            )
            self._client = cast(_OpenAIEmbeddingClientPort, client)
        return self._client

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        """Embed one bounded batch with SDK retries disabled."""

        inputs = _validate_inputs(texts, timeout_seconds)
        try:
            if self._model is None:
                raise EmbeddingProviderConfigurationError(
                    EMBEDDING_CONFIGURATION_MESSAGE
                )
            client = self._get_client().with_options(
                timeout=timeout_seconds,
                max_retries=0,
            )
            response = client.embeddings.create(
                model=self._model,
                input=list(inputs),
                dimensions=EMBEDDING_DIMENSIONS,
                encoding_format="float",
            )
            return _normalize_response(response, expected_count=len(inputs))
        except Exception as error:
            _raise_normalized_error(error)
