"""Deterministic offline tests for code-owned reciprocal rank fusion."""

from uuid import UUID

import pytest

from app.agent.retrieval import (
    MAX_RETRIEVAL_CANDIDATES,
    RRF_RANK_CONSTANT,
    RetrievalFusionError,
    reciprocal_rank_fuse,
)


def _id(value: int) -> UUID:
    return UUID(int=value)


def test_rrf_handles_lexical_vector_overlap_and_zero_results() -> None:
    vector_only, overlap, lexical_only = _id(1), _id(2), _id(3)

    fused = reciprocal_rank_fuse(
        vector_chunk_ids=(vector_only, overlap),
        lexical_chunk_ids=(overlap, lexical_only),
        limit=3,
    )

    assert [item.chunk_id for item in fused] == [overlap, vector_only, lexical_only]
    assert fused[0].vector_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].fusion_score == pytest.approx(
        1 / (RRF_RANK_CONSTANT + 2) + 1 / (RRF_RANK_CONSTANT + 1)
    )
    assert fused[1].lexical_rank is None
    assert fused[2].vector_rank is None
    assert (
        reciprocal_rank_fuse(vector_chunk_ids=(), lexical_chunk_ids=(), limit=20) == ()
    )


def test_rrf_uses_chunk_id_as_stable_tie_break() -> None:
    smaller, larger = _id(10), _id(20)

    fused = reciprocal_rank_fuse(
        vector_chunk_ids=(larger,),
        lexical_chunk_ids=(smaller,),
        limit=2,
    )

    assert [item.chunk_id for item in fused] == [smaller, larger]
    assert fused[0].fusion_score == fused[1].fusion_score


def test_rrf_deduplicates_overlap_and_caps_final_results() -> None:
    vector_ids = tuple(_id(value) for value in range(1, 21))
    lexical_ids = tuple(_id(value) for value in range(21, 41))

    fused = reciprocal_rank_fuse(
        vector_chunk_ids=vector_ids,
        lexical_chunk_ids=lexical_ids,
        limit=20,
    )

    assert len(fused) == 20
    assert len({item.chunk_id for item in fused}) == 20


@pytest.mark.parametrize(
    ("vector_ids", "lexical_ids", "limit"),
    [
        (tuple(_id(value) for value in range(1, 42)), (), 20),
        ((), tuple(_id(value) for value in range(1, 42)), 20),
        ((_id(1), _id(1)), (), 20),
        ((), (), 0),
        ((), (), 21),
    ],
)
def test_rrf_rejects_unbounded_duplicate_or_invalid_inputs(
    vector_ids: tuple[UUID, ...],
    lexical_ids: tuple[UUID, ...],
    limit: int,
) -> None:
    assert MAX_RETRIEVAL_CANDIDATES == 40
    with pytest.raises(RetrievalFusionError):
        reciprocal_rank_fuse(
            vector_chunk_ids=vector_ids,
            lexical_chunk_ids=lexical_ids,
            limit=limit,
        )
