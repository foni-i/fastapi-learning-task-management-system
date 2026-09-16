"""Final Stage 11 PostgreSQL storage, retrieval, and grounding proof."""

from collections.abc import Sequence
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.agent.embeddings import EMBEDDING_DIMENSIONS, EmbeddingBatch
from app.agent.grounding import build_grounded_knowledge, validate_citation_references
from app.models import KnowledgeDocument, KnowledgeDocumentChunk, User
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery
from app.services.knowledge_retrieval import search_owned_knowledge

pytestmark = pytest.mark.integration

STAGE_11_HEAD = "e3b7c2d9a410"


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


class _QueryEmbeddingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        assert texts == ("alpha",)
        assert timeout_seconds == 5
        self.calls += 1
        return (tuple(_unit_vector(0)),)


def test_stage11_catalog_owner_retrieval_and_grounding_contract(
    db_session: Session,
) -> None:
    connection = db_session.connection()
    assert (
        MigrationContext.configure(connection).get_current_revision() == STAGE_11_HEAD
    )
    inspector = inspect(connection)
    assert {"knowledge_documents", "knowledge_document_chunks"}.issubset(
        inspector.get_table_names()
    )
    columns = {
        column["name"]: column
        for column in inspector.get_columns("knowledge_document_chunks")
    }
    assert str(columns["embedding"]["type"]) == "VECTOR(1536)"
    assert columns["search_vector"]["computed"] is not None
    assert {
        item["name"] for item in inspector.get_foreign_keys("knowledge_document_chunks")
    } == {"fk_knowledge_document_chunks_document_owner"}
    index_rows = connection.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'knowledge_document_chunks'"
        )
    ).tuples()
    index_definitions = {
        str(index_name): str(definition) for index_name, definition in index_rows
    }
    assert (
        "USING gin (search_vector)"
        in index_definitions["ix_knowledge_document_chunks_search_vector"]
    )
    assert (
        "USING hnsw (embedding vector_cosine_ops)"
        in index_definitions["ix_knowledge_document_chunks_embedding_hnsw"]
    )
    assert (
        connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        == "0.8.6"
    )

    owner = User(
        email=f"stage11-owner-{uuid4()}@example.com",
        password_hash="synthetic-test-hash",
    )
    foreign = User(
        email=f"stage11-foreign-{uuid4()}@example.com",
        password_hash="synthetic-test-hash",
    )
    db_session.add_all((owner, foreign))
    db_session.flush()
    owner_document = KnowledgeDocument(
        user_id=owner.id,
        display_name="owner-synthetic.txt",
        media_type="text/plain",
        byte_count=20,
        content_sha256="a" * 64,
        page_count=1,
        extracted_text="owner synthetic source",
        status="INDEXED",
    )
    foreign_document = KnowledgeDocument(
        user_id=foreign.id,
        display_name="foreign-synthetic.txt",
        media_type="text/plain",
        byte_count=22,
        content_sha256="b" * 64,
        page_count=1,
        extracted_text="foreign synthetic source",
        status="INDEXED",
    )
    db_session.add_all((owner_document, foreign_document))
    db_session.flush()
    owner_chunk = KnowledgeDocumentChunk(
        document_id=owner_document.id,
        user_id=owner.id,
        page_number=1,
        ordinal=0,
        content="alpha owner evidence",
        content_fingerprint="c" * 64,
        embedding=_unit_vector(1),
    )
    foreign_chunk = KnowledgeDocumentChunk(
        document_id=foreign_document.id,
        user_id=foreign.id,
        page_number=1,
        ordinal=0,
        content="alpha alpha foreign private evidence",
        content_fingerprint="d" * 64,
        embedding=_unit_vector(0),
    )
    db_session.add_all((owner_chunk, foreign_chunk))
    db_session.flush()

    provider = _QueryEmbeddingProvider()
    result = search_owned_knowledge(
        KnowledgeSearchQuery(query="alpha", top_k=10),
        owner.id,
        db_session,
        embedding_provider=provider,
        timeout_seconds=5,
    )

    assert provider.calls == 1
    assert len(result.items) == 1
    assert result.items[0].chunk_id == owner_chunk.id
    assert foreign_chunk.id not in {item.chunk_id for item in result.items}
    assert "foreign private" not in result.model_dump_json()
    grounding = build_grounded_knowledge(result)
    validate_citation_references(
        ((grounding.evidence[0].citation_id,),),
        grounding,
    )
    assert grounding.evidence[0].excerpt == "alpha owner evidence"
