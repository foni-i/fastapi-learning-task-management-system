"""Offline tests for bounded owner-scoped vector retrieval."""

from math import nan
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
)
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkMatch,
    KnowledgeDocumentChunkRepository,
    KnowledgeDocumentLexicalMatch,
)
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery
from app.services.knowledge_retrieval import (
    KNOWLEDGE_RETRIEVAL_MESSAGE,
    KnowledgeRetrievalError,
    search_owned_knowledge,
)
from tests.fakes.embedding_provider import DeterministicEmbeddingProvider


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 2_001},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": 21},
        {"query": "x", "document_ids": [str(uuid4())] * 21},
        {"query": "x", "user_id": str(uuid4())},
        {"query": "x", "session": "private"},
        {"query": "x", "sql": "select secret"},
        {"query": "x", "embedding": [0.1]},
        {"query": "x", "operator": "<=>"},
    ],
)
def test_search_query_rejects_blank_unbounded_and_privileged_input(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchQuery.model_validate(payload)


def test_search_query_accepts_exact_bounds_and_normalizes_text() -> None:
    document_ids = tuple(uuid4() for _ in range(20))

    minimum = KnowledgeSearchQuery(query="  x  ", top_k=1)
    maximum = KnowledgeSearchQuery(
        query="x" * 2_000,
        top_k=20,
        document_ids=document_ids,
    )

    assert minimum.query == "x"
    assert KnowledgeSearchQuery(query="x").top_k == 10
    assert len(maximum.document_ids or ()) == 20


class RecordingRepository:
    def __init__(
        self,
        vector_matches: tuple[KnowledgeDocumentChunkMatch, ...],
        lexical_matches: tuple[KnowledgeDocumentLexicalMatch, ...] = (),
    ) -> None:
        self.vector_matches = vector_matches
        self.lexical_matches = lexical_matches
        self.vector_calls: list[dict[str, object]] = []
        self.lexical_calls: list[dict[str, object]] = []

    def search_vector_owned(
        self, **kwargs: object
    ) -> tuple[KnowledgeDocumentChunkMatch, ...]:
        self.vector_calls.append(kwargs)
        return self.vector_matches

    def search_lexical_owned(
        self, **kwargs: object
    ) -> tuple[KnowledgeDocumentLexicalMatch, ...]:
        self.lexical_calls.append(kwargs)
        return self.lexical_matches


def _match(
    *,
    content: str = "private",
    distance: float = 0.25,
    chunk_id: UUID | None = None,
    document_id: UUID | None = None,
) -> KnowledgeDocumentChunkMatch:
    return KnowledgeDocumentChunkMatch(
        chunk_id=chunk_id if chunk_id is not None else uuid4(),
        document_id=document_id if document_id is not None else uuid4(),
        source="notes.txt",
        page_number=2,
        ordinal=3,
        content=content,
        distance=distance,
    )


def _lexical_match(
    match: KnowledgeDocumentChunkMatch, *, score: float = 0.5
) -> KnowledgeDocumentLexicalMatch:
    return KnowledgeDocumentLexicalMatch(
        chunk_id=match.chunk_id,
        document_id=match.document_id,
        source=match.source,
        page_number=match.page_number,
        ordinal=match.ordinal,
        content=match.content,
        score=score,
    )


def test_service_embeds_once_and_returns_stable_bounded_public_citation() -> None:
    provider = DeterministicEmbeddingProvider()
    match = _match(content="z" * 700)
    repository = RecordingRepository((match,))
    session = MagicMock(spec=Session)
    document_id = uuid4()

    result = search_owned_knowledge(
        KnowledgeSearchQuery(
            query="semantic query", top_k=4, document_ids=(document_id,)
        ),
        uuid4(),
        session,
        embedding_provider=provider,
        timeout_seconds=7.5,
        repository_factory=lambda _session: repository,
    )

    assert len(provider.calls) == 1
    assert provider.calls[0].input_count == 1
    assert provider.calls[0].input_lengths == (len("semantic query"),)
    call = repository.vector_calls[0]
    assert len(cast(tuple[float, ...], call["query_embedding"])) == 1_536
    assert call["top_k"] == 40
    assert call["document_ids"] == (document_id,)
    assert repository.lexical_calls == [
        {
            "user_id": repository.vector_calls[0]["user_id"],
            "query": "semantic query",
            "top_k": 40,
            "document_ids": (document_id,),
        }
    ]
    citation = result.items[0]
    assert citation.citation_id == f"knowledge:{match.document_id}:{match.chunk_id}"
    assert len(citation.excerpt) == 500
    assert citation.vector_rank == 1
    assert citation.lexical_rank is None
    assert citation.fusion_score == pytest.approx(1 / 61)
    assert "user_id" not in citation.model_dump()
    assert "embedding" not in citation.model_dump()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_service_fuses_overlap_and_returns_bounded_rank_evidence() -> None:
    match = _match(content="hostile instructions are only excerpt data")
    repository = RecordingRepository((match,), (_lexical_match(match),))

    result = search_owned_knowledge(
        KnowledgeSearchQuery(query="hostile", top_k=1),
        uuid4(),
        MagicMock(spec=Session),
        embedding_provider=DeterministicEmbeddingProvider(),
        timeout_seconds=5,
        repository_factory=lambda _session: repository,
    )

    citation = result.items[0]
    assert citation.vector_rank == 1
    assert citation.lexical_rank == 1
    assert citation.fusion_score == pytest.approx(2 / 61)
    assert citation.distance == pytest.approx(match.distance)
    assert citation.excerpt == match.content


def test_service_handles_lexical_only_citation_without_page_number() -> None:
    lexical_match = KnowledgeDocumentLexicalMatch(
        chunk_id=uuid4(),
        document_id=uuid4(),
        source="page-free.txt",
        page_number=None,
        ordinal=0,
        content="lexical evidence",
        score=0.25,
    )
    repository = RecordingRepository((), (lexical_match,))

    result = search_owned_knowledge(
        KnowledgeSearchQuery(query="lexical"),
        uuid4(),
        MagicMock(spec=Session),
        embedding_provider=DeterministicEmbeddingProvider(),
        timeout_seconds=5,
        repository_factory=lambda _session: repository,
    )

    citation = result.items[0]
    assert citation.page_number is None
    assert citation.distance is None
    assert citation.vector_rank is None
    assert citation.lexical_rank == 1


class FailingProvider:
    def embed(self, texts: object, *, timeout_seconds: float) -> object:
        raise EmbeddingProviderUnavailableError("private provider response")


class FailingRepository:
    def search_vector_owned(
        self, **kwargs: object
    ) -> tuple[KnowledgeDocumentChunkMatch, ...]:
        raise SQLAlchemyError("private database detail")

    def search_lexical_owned(
        self, **kwargs: object
    ) -> tuple[KnowledgeDocumentLexicalMatch, ...]:
        return ()


class FailingLexicalRepository(RecordingRepository):
    def search_lexical_owned(
        self, **kwargs: object
    ) -> tuple[KnowledgeDocumentLexicalMatch, ...]:
        raise SQLAlchemyError("private full-text database detail")


@pytest.mark.parametrize(
    ("provider", "repository"),
    [
        (cast(EmbeddingProvider, FailingProvider()), RecordingRepository(())),
        (
            DeterministicEmbeddingProvider(dimensions=EMBEDDING_DIMENSIONS - 1),
            RecordingRepository(()),
        ),
        (DeterministicEmbeddingProvider(), FailingRepository()),
        (
            DeterministicEmbeddingProvider(),
            RecordingRepository((_match(distance=nan),)),
        ),
        (
            DeterministicEmbeddingProvider(),
            RecordingRepository((), (_lexical_match(_match(), score=nan),)),
        ),
        (DeterministicEmbeddingProvider(), FailingLexicalRepository(())),
        (
            DeterministicEmbeddingProvider(),
            RecordingRepository(tuple(_match() for _ in range(41))),
        ),
    ],
)
def test_service_normalizes_provider_vector_database_and_distance_failures(
    provider: EmbeddingProvider,
    repository: object,
) -> None:
    with pytest.raises(
        KnowledgeRetrievalError, match=KNOWLEDGE_RETRIEVAL_MESSAGE
    ) as exc_info:
        search_owned_knowledge(
            KnowledgeSearchQuery(query="private query"),
            uuid4(),
            MagicMock(spec=Session),
            embedding_provider=provider,
            timeout_seconds=5,
            repository_factory=lambda _session: cast(RecordingRepository, repository),
        )

    message = str(exc_info.value)
    assert "private query" not in message
    assert "private provider" not in message
    assert "private database" not in message


def test_repository_sql_filters_owner_and_documents_before_stable_ranking() -> None:
    session = MagicMock(spec=Session)
    session.execute.return_value.tuples.return_value.all.return_value = []
    user_id = uuid4()
    document_ids = (uuid4(), uuid4())

    result = KnowledgeDocumentChunkRepository(session).search_vector_owned(
        user_id=user_id,
        query_embedding=(0.5,) * EMBEDDING_DIMENSIONS,
        top_k=7,
        document_ids=document_ids,
    )

    assert result == ()
    statement = session.execute.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
        )
    )
    assert "knowledge_document_chunks.user_id =" in sql
    assert "knowledge_documents.user_id =" in sql
    assert "knowledge_document_chunks.document_id IN" in sql
    assert "knowledge_document_chunks.embedding <=>" in sql
    assert "ORDER BY distance ASC, knowledge_document_chunks.id ASC" in sql
    assert "LIMIT" in sql
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()


def test_lexical_repository_uses_bound_simple_query_after_owner_filters() -> None:
    session = MagicMock(spec=Session)
    session.execute.return_value.tuples.return_value.all.return_value = []
    user_id = uuid4()
    document_ids = (uuid4(),)
    hostile_query = "secret | !operator <->"

    result = KnowledgeDocumentChunkRepository(session).search_lexical_owned(
        user_id=user_id,
        query=hostile_query,
        top_k=40,
        document_ids=document_ids,
    )

    assert result == ()
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
    )
    sql = str(compiled)
    assert "knowledge_document_chunks.user_id =" in sql
    assert "knowledge_documents.user_id =" in sql
    assert "knowledge_document_chunks.document_id IN" in sql
    assert "plainto_tsquery" in sql
    assert "knowledge_document_chunks.search_vector @@" in sql
    assert "ORDER BY lexical_score DESC, knowledge_document_chunks.id ASC" in sql
    assert "LIMIT" in sql
    assert hostile_query not in sql
    assert hostile_query in compiled.params.values()
    assert "simple" in compiled.params.values()
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
