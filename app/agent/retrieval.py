"""Deterministic, code-owned ranking helpers for knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

RRF_RANK_CONSTANT = 60
MAX_RETRIEVAL_CANDIDATES = 40
MAX_FUSED_RESULTS = 20


class RetrievalFusionError(ValueError):
    """Raised when bounded ranked inputs cannot be fused safely."""


@dataclass(frozen=True, slots=True)
class FusedChunkRank:
    """Rank evidence without persistence-layer data."""

    chunk_id: UUID
    vector_rank: int | None
    lexical_rank: int | None
    fusion_score: float


def reciprocal_rank_fuse(
    *,
    vector_chunk_ids: tuple[UUID, ...],
    lexical_chunk_ids: tuple[UUID, ...],
    limit: int,
) -> tuple[FusedChunkRank, ...]:
    """Fuse two already-ranked candidate lists with a fixed RRF formula."""
    if not 1 <= limit <= MAX_FUSED_RESULTS:
        raise RetrievalFusionError("Invalid fusion result limit.")
    _validate_ranked_ids(vector_chunk_ids)
    _validate_ranked_ids(lexical_chunk_ids)

    vector_ranks = {
        chunk_id: rank for rank, chunk_id in enumerate(vector_chunk_ids, start=1)
    }
    lexical_ranks = {
        chunk_id: rank for rank, chunk_id in enumerate(lexical_chunk_ids, start=1)
    }
    fused: list[FusedChunkRank] = []
    for chunk_id in vector_ranks.keys() | lexical_ranks.keys():
        vector_rank = vector_ranks.get(chunk_id)
        lexical_rank = lexical_ranks.get(chunk_id)
        score = 0.0
        if vector_rank is not None:
            score += 1.0 / (RRF_RANK_CONSTANT + vector_rank)
        if lexical_rank is not None:
            score += 1.0 / (RRF_RANK_CONSTANT + lexical_rank)
        fused.append(
            FusedChunkRank(
                chunk_id=chunk_id,
                vector_rank=vector_rank,
                lexical_rank=lexical_rank,
                fusion_score=score,
            )
        )

    fused.sort(key=lambda item: (-item.fusion_score, item.chunk_id.int))
    return tuple(fused[:limit])


def _validate_ranked_ids(chunk_ids: tuple[UUID, ...]) -> None:
    if len(chunk_ids) > MAX_RETRIEVAL_CANDIDATES:
        raise RetrievalFusionError("Too many retrieval candidates.")
    if len(set(chunk_ids)) != len(chunk_ids):
        raise RetrievalFusionError("Duplicate retrieval candidate.")
