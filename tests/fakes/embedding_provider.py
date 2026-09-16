"""Deterministic offline embeddings with bounded safe call metadata."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from app.agent.embeddings import EMBEDDING_DIMENSIONS, EmbeddingBatch


@dataclass(frozen=True, slots=True)
class RecordedEmbeddingCall:
    """Record sizes and timeout without retaining private chunk text."""

    input_count: int
    input_lengths: tuple[int, ...]
    timeout_seconds: float


class DeterministicEmbeddingProvider:
    """Generate stable finite vectors locally without provider access."""

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[RecordedEmbeddingCall] = []

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        self.calls.append(
            RecordedEmbeddingCall(
                input_count=len(texts),
                input_lengths=tuple(len(text) for text in texts),
                timeout_seconds=timeout_seconds,
            )
        )
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            vectors.append(
                tuple(
                    (digest[index % len(digest)] + 1) / 256
                    for index in range(self.dimensions)
                )
            )
        return tuple(vectors)
