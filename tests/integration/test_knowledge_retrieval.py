"""Real pgvector proof for owner-filtered cosine retrieval."""

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agent.embeddings import EMBEDDING_DIMENSIONS, EmbeddingBatch
from app.models import KnowledgeDocument, KnowledgeDocumentChunk, User
from app.repositories.knowledge_document_chunks import KnowledgeDocumentChunkRepository
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery
from app.services.knowledge_retrieval import search_owned_knowledge

pytestmark = pytest.mark.integration


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


class QueryEmbeddingProvider:
    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = tuple(vector)
        self.calls = 0

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        assert len(texts) == 1
        assert timeout_seconds == 5
        self.calls += 1
        return (self.vector,)


def _document(*, document_id: UUID, user_id: UUID, name: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=document_id,
        user_id=user_id,
        display_name=name,
        media_type="text/plain",
        byte_count=10,
        content_sha256=uuid4().hex * 2,
        page_count=1,
        extracted_text="private source text",
        status="INDEXED",
    )


def _chunk(
    *,
    chunk_id: UUID,
    document_id: UUID,
    user_id: UUID,
    ordinal: int,
    content: str,
    embedding: list[float],
) -> KnowledgeDocumentChunk:
    return KnowledgeDocumentChunk(
        id=chunk_id,
        document_id=document_id,
        user_id=user_id,
        page_number=1,
        ordinal=ordinal,
        content=content,
        content_fingerprint=uuid4().hex * 2,
        embedding=embedding,
    )


def test_pgvector_filters_owner_and_documents_before_stable_cosine_ranking(
    integration_engine: Engine,
) -> None:
    owner_id, foreign_id = uuid4(), uuid4()
    owner_document_id, filtered_document_id, foreign_document_id = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    tied_chunk_ids = sorted((uuid4(), uuid4()))
    closer_chunk_id, foreign_closest_chunk_id = uuid4(), uuid4()

    with Session(integration_engine) as session:
        session.add_all(
            [
                User(
                    id=owner_id,
                    email=f"retrieval-owner-{uuid4()}@example.com",
                    password_hash="synthetic-test-hash",
                ),
                User(
                    id=foreign_id,
                    email=f"retrieval-foreign-{uuid4()}@example.com",
                    password_hash="synthetic-test-hash",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                _document(
                    document_id=owner_document_id,
                    user_id=owner_id,
                    name="owner-notes.txt",
                ),
                _document(
                    document_id=filtered_document_id,
                    user_id=owner_id,
                    name="owner-guide.txt",
                ),
                _document(
                    document_id=foreign_document_id,
                    user_id=foreign_id,
                    name="foreign-secret.txt",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                _chunk(
                    chunk_id=tied_chunk_ids[1],
                    document_id=owner_document_id,
                    user_id=owner_id,
                    ordinal=0,
                    content="owner tie later",
                    embedding=_unit_vector(1),
                ),
                _chunk(
                    chunk_id=tied_chunk_ids[0],
                    document_id=owner_document_id,
                    user_id=owner_id,
                    ordinal=1,
                    content="owner tie earlier",
                    embedding=_unit_vector(1),
                ),
                _chunk(
                    chunk_id=closer_chunk_id,
                    document_id=filtered_document_id,
                    user_id=owner_id,
                    ordinal=0,
                    content="owner closer",
                    embedding=[0.8, 0.6, *([0.0] * (EMBEDDING_DIMENSIONS - 2))],
                ),
                _chunk(
                    chunk_id=foreign_closest_chunk_id,
                    document_id=foreign_document_id,
                    user_id=foreign_id,
                    ordinal=0,
                    content="foreign closest private text",
                    embedding=_unit_vector(0),
                ),
            ]
        )
        session.commit()

    provider = QueryEmbeddingProvider(_unit_vector(0))
    try:
        with Session(integration_engine) as session:
            repository_matches = KnowledgeDocumentChunkRepository(
                session
            ).search_vector_owned(
                user_id=owner_id,
                query_embedding=_unit_vector(0),
                top_k=2,
                document_ids=None,
            )
            assert repository_matches[0].chunk_id == closer_chunk_id
            nearest = search_owned_knowledge(
                KnowledgeSearchQuery(query="owner query", top_k=2),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )
            tied = search_owned_knowledge(
                KnowledgeSearchQuery(
                    query="owner query",
                    top_k=2,
                    document_ids=(owner_document_id,),
                ),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )
            foreign_filter = search_owned_knowledge(
                KnowledgeSearchQuery(
                    query="owner query",
                    document_ids=(foreign_document_id,),
                ),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )

        assert provider.calls == 3
        assert nearest.items[0].chunk_id == closer_chunk_id
        assert nearest.items[0].distance == pytest.approx(0.2)
        assert foreign_closest_chunk_id not in {item.chunk_id for item in nearest.items}
        assert [item.chunk_id for item in tied.items] == tied_chunk_ids
        assert {item.document_id for item in tied.items} == {owner_document_id}
        assert foreign_filter.items == ()
        assert "foreign" not in nearest.model_dump_json()
    finally:
        with Session(integration_engine) as session:
            session.execute(
                delete(KnowledgeDocumentChunk).where(
                    KnowledgeDocumentChunk.user_id.in_((owner_id, foreign_id))
                )
            )
            session.execute(
                delete(KnowledgeDocument).where(
                    KnowledgeDocument.user_id.in_((owner_id, foreign_id))
                )
            )
            session.execute(delete(User).where(User.id.in_((owner_id, foreign_id))))
            session.commit()


def test_pgvector_hybrid_fusion_is_stable_bounded_and_owner_scoped(
    integration_engine: Engine,
) -> None:
    owner_id, foreign_id = uuid4(), uuid4()
    lexical_document_id, vector_document_id, foreign_document_id = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    lexical_only_id, vector_only_id, overlap_id = sorted((uuid4(), uuid4(), uuid4()))
    decoy_ids = tuple(uuid4() for _ in range(39))

    with Session(integration_engine) as session:
        session.add_all(
            [
                User(
                    id=owner_id,
                    email=f"hybrid-owner-{uuid4()}@example.com",
                    password_hash="synthetic-test-hash",
                ),
                User(
                    id=foreign_id,
                    email=f"hybrid-foreign-{uuid4()}@example.com",
                    password_hash="synthetic-test-hash",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                _document(
                    document_id=lexical_document_id,
                    user_id=owner_id,
                    name="lexical-owner.txt",
                ),
                _document(
                    document_id=vector_document_id,
                    user_id=owner_id,
                    name="vector-owner.txt",
                ),
                _document(
                    document_id=foreign_document_id,
                    user_id=foreign_id,
                    name="foreign-secret.txt",
                ),
            ]
        )
        session.flush()
        chunks = [
            _chunk(
                chunk_id=lexical_only_id,
                document_id=lexical_document_id,
                user_id=owner_id,
                ordinal=0,
                content="alpha alpha alpha alpha lexical evidence",
                embedding=_unit_vector(1),
            ),
            _chunk(
                chunk_id=overlap_id,
                document_id=lexical_document_id,
                user_id=owner_id,
                ordinal=1,
                content="alpha overlap evidence",
                embedding=[
                    0.9,
                    0.435889894,
                    *([0.0] * (EMBEDDING_DIMENSIONS - 2)),
                ],
            ),
            _chunk(
                chunk_id=vector_only_id,
                document_id=vector_document_id,
                user_id=owner_id,
                ordinal=0,
                content="semantic-only evidence",
                embedding=_unit_vector(0),
            ),
            _chunk(
                chunk_id=uuid4(),
                document_id=foreign_document_id,
                user_id=foreign_id,
                ordinal=0,
                content="alpha alpha alpha alpha alpha foreign secret",
                embedding=_unit_vector(0),
            ),
        ]
        chunks.extend(
            _chunk(
                chunk_id=chunk_id,
                document_id=vector_document_id,
                user_id=owner_id,
                ordinal=ordinal,
                content=f"bounded vector decoy {ordinal}",
                embedding=[
                    0.5,
                    0.866025404,
                    *([0.0] * (EMBEDDING_DIMENSIONS - 2)),
                ],
            )
            for ordinal, chunk_id in enumerate(decoy_ids, start=1)
        )
        session.add_all(chunks)
        session.commit()

    provider = QueryEmbeddingProvider(_unit_vector(0))
    try:
        with Session(integration_engine) as session:
            result = search_owned_knowledge(
                KnowledgeSearchQuery(query="alpha", top_k=3),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )
            document_filtered = search_owned_knowledge(
                KnowledgeSearchQuery(
                    query="alpha",
                    top_k=3,
                    document_ids=(lexical_document_id,),
                ),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )
            foreign_filtered = search_owned_knowledge(
                KnowledgeSearchQuery(
                    query="alpha",
                    document_ids=(foreign_document_id,),
                ),
                owner_id,
                session,
                embedding_provider=provider,
                timeout_seconds=5,
            )
        assert provider.calls == 3
        assert result.items[0].chunk_id == overlap_id
        assert result.items[0].vector_rank == 2
        assert result.items[0].lexical_rank == 2
        assert result.items[0].fusion_score == pytest.approx(2 / 62)
        assert [item.chunk_id for item in result.items[1:]] == [
            lexical_only_id,
            vector_only_id,
        ]
        assert result.items[1].vector_rank is None
        assert result.items[1].lexical_rank == 1
        assert result.items[2].vector_rank == 1
        assert result.items[2].lexical_rank is None
        assert {item.document_id for item in document_filtered.items} == {
            lexical_document_id
        }
        assert foreign_filtered.items == ()
        assert "foreign secret" not in result.model_dump_json()
    finally:
        with Session(integration_engine) as session:
            session.execute(
                delete(KnowledgeDocumentChunk).where(
                    KnowledgeDocumentChunk.user_id.in_((owner_id, foreign_id))
                )
            )
            session.execute(
                delete(KnowledgeDocument).where(
                    KnowledgeDocument.user_id.in_((owner_id, foreign_id))
                )
            )
            session.execute(delete(User).where(User.id.in_((owner_id, foreign_id))))
            session.commit()
