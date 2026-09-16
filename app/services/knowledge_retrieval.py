"""Owner-scoped hybrid retrieval with safe public projection."""

from collections.abc import Callable, Sequence
from math import isfinite
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    validate_embedding_batch,
)
from app.agent.retrieval import (
    MAX_RETRIEVAL_CANDIDATES,
    RetrievalFusionError,
    reciprocal_rank_fuse,
)
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkMatch,
    KnowledgeDocumentChunkRepository,
    KnowledgeDocumentLexicalMatch,
)
from app.schemas.knowledge_retrieval import (
    MAX_KNOWLEDGE_EXCERPT_CHARACTERS,
    KnowledgeCitation,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)

KNOWLEDGE_RETRIEVAL_MESSAGE = "Knowledge search is temporarily unavailable"


class KnowledgeRetrievalError(Exception):
    """Expose one fixed failure without query, document, vector, or database details."""


class KnowledgeChunkSearchRepository(Protocol):
    def search_vector_owned(
        self,
        *,
        user_id: UUID,
        query_embedding: Sequence[float],
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> Sequence[KnowledgeDocumentChunkMatch]: ...

    def search_lexical_owned(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> Sequence[KnowledgeDocumentLexicalMatch]: ...


KnowledgeChunkSearchRepositoryFactory = Callable[
    [Session], KnowledgeChunkSearchRepository
]
type KnowledgeCandidate = KnowledgeDocumentChunkMatch | KnowledgeDocumentLexicalMatch


def _citation_id(match: KnowledgeCandidate) -> str:
    return f"knowledge:{match.document_id}:{match.chunk_id}"


def _candidate_identity(match: KnowledgeCandidate) -> tuple[object, ...]:
    return (
        match.document_id,
        match.source,
        match.page_number,
        match.ordinal,
        match.content,
    )


def search_owned_knowledge(
    search_query: KnowledgeSearchQuery,
    user_id: UUID,
    session: Session,
    *,
    embedding_provider: EmbeddingProvider,
    timeout_seconds: float,
    repository_factory: KnowledgeChunkSearchRepositoryFactory = (
        KnowledgeDocumentChunkRepository
    ),
) -> KnowledgeSearchResult:
    """Embed once, fuse two owner-filtered rankings, and project safe citations."""

    try:
        raw_vectors = embedding_provider.embed(
            (search_query.query,),
            timeout_seconds=timeout_seconds,
        )
        query_vector = validate_embedding_batch(raw_vectors, expected_count=1)[0]
        repository = repository_factory(session)
        vector_matches = tuple(
            repository.search_vector_owned(
                user_id=user_id,
                query_embedding=query_vector,
                top_k=MAX_RETRIEVAL_CANDIDATES,
                document_ids=search_query.document_ids,
            )
        )
        lexical_matches = tuple(
            repository.search_lexical_owned(
                user_id=user_id,
                query=search_query.query,
                top_k=MAX_RETRIEVAL_CANDIDATES,
                document_ids=search_query.document_ids,
            )
        )

        candidates: dict[UUID, KnowledgeCandidate] = {}
        distances: dict[UUID, float] = {}
        for vector_match in vector_matches:
            if (
                not isfinite(vector_match.distance)
                or not 0 <= vector_match.distance <= 2
            ):
                raise RetrievalFusionError("Invalid vector distance.")
            candidates[vector_match.chunk_id] = vector_match
            distances[vector_match.chunk_id] = vector_match.distance
        for lexical_match in lexical_matches:
            if not isfinite(lexical_match.score) or lexical_match.score < 0:
                raise RetrievalFusionError("Invalid lexical score.")
            existing = candidates.get(lexical_match.chunk_id)
            if existing is not None and _candidate_identity(
                existing
            ) != _candidate_identity(lexical_match):
                raise RetrievalFusionError("Inconsistent retrieval candidate.")
            candidates.setdefault(lexical_match.chunk_id, lexical_match)

        fused = reciprocal_rank_fuse(
            vector_chunk_ids=tuple(match.chunk_id for match in vector_matches),
            lexical_chunk_ids=tuple(match.chunk_id for match in lexical_matches),
            limit=search_query.top_k,
        )
        citations = tuple(
            KnowledgeCitation(
                citation_id=_citation_id(candidates[rank.chunk_id]),
                document_id=candidates[rank.chunk_id].document_id,
                chunk_id=rank.chunk_id,
                source=candidates[rank.chunk_id].source,
                page_number=candidates[rank.chunk_id].page_number,
                ordinal=candidates[rank.chunk_id].ordinal,
                distance=distances.get(rank.chunk_id),
                vector_rank=rank.vector_rank,
                lexical_rank=rank.lexical_rank,
                fusion_score=rank.fusion_score,
                excerpt=candidates[rank.chunk_id].content[
                    :MAX_KNOWLEDGE_EXCERPT_CHARACTERS
                ],
            )
            for rank in fused
        )
        return KnowledgeSearchResult(items=citations)
    except KnowledgeRetrievalError:
        raise
    except (
        EmbeddingProviderError,
        RetrievalFusionError,
        SQLAlchemyError,
        ValidationError,
    ):
        raise KnowledgeRetrievalError(KNOWLEDGE_RETRIEVAL_MESSAGE) from None
