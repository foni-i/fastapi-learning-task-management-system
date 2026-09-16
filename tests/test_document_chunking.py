"""Deterministic document chunk and storage metadata contracts."""

from typing import cast

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Computed, Table
from sqlalchemy.schema import ForeignKeyConstraint, UniqueConstraint

from app.core.exceptions import KnowledgeDocumentIndexingError
from app.models import KnowledgeDocumentChunk
from app.services.knowledge_document_indexing import (
    CHUNK_OVERLAP_CHARACTERS,
    MAX_CHUNK_CHARACTERS,
    chunk_document_text,
)


def test_chunk_boundaries_overlap_unicode_and_fingerprint_are_stable() -> None:
    text = "学" * (MAX_CHUNK_CHARACTERS + 1)

    first = chunk_document_text(text, page_count=1)
    second = chunk_document_text(text, page_count=1)

    assert first == second
    assert [len(chunk.content) for chunk in first] == [2000, 201]
    assert (
        first[0].content[-CHUNK_OVERLAP_CHARACTERS:]
        == (first[1].content[:CHUNK_OVERLAP_CHARACTERS])
    )
    assert [chunk.ordinal for chunk in first] == [0, 1]
    assert all(len(chunk.content_fingerprint) == 64 for chunk in first)


def test_chunking_never_crosses_pdf_page_boundaries() -> None:
    chunks = chunk_document_text("first page\fsecond page", page_count=2)

    assert [(item.page_number, item.ordinal, item.content) for item in chunks] == [
        (1, 0, "first page"),
        (2, 1, "second page"),
    ]


def test_chunking_accepts_exact_maximum_and_rejects_one_more() -> None:
    exactly_two_per_page = "x" * 2001
    accepted = chunk_document_text(
        "\f".join([exactly_two_per_page] * 100),
        page_count=100,
    )
    assert len(accepted) == 200

    oversized = "\f".join(["x" * 4001, *([exactly_two_per_page] * 99)])
    with pytest.raises(KnowledgeDocumentIndexingError):
        chunk_document_text(oversized, page_count=100)


def test_chunking_rejects_inconsistent_or_empty_private_text() -> None:
    with pytest.raises(KnowledgeDocumentIndexingError):
        chunk_document_text("one page only", page_count=2)
    with pytest.raises(KnowledgeDocumentIndexingError):
        chunk_document_text("", page_count=1)


def test_chunk_model_declares_private_vector_and_named_integrity_contract() -> None:
    table = cast(Table, KnowledgeDocumentChunk.__table__)
    assert set(table.columns.keys()) == {
        "id",
        "document_id",
        "user_id",
        "page_number",
        "ordinal",
        "content",
        "content_fingerprint",
        "embedding",
        "search_vector",
    }
    assert isinstance(table.c.embedding.type, Vector)
    assert table.c.embedding.type.dim == 1536
    assert isinstance(table.c.search_vector.computed, Computed)
    assert "simple" in str(table.c.search_vector.computed.sqltext)
    constraints = {item.name: item for item in table.constraints}
    assert set(constraints) == {
        "pk_knowledge_document_chunks",
        "fk_knowledge_document_chunks_document_owner",
        "uq_knowledge_document_chunks_document_ordinal",
        "ck_knowledge_document_chunks_page_number",
        "ck_knowledge_document_chunks_ordinal",
        "ck_knowledge_document_chunks_content_length",
        "ck_knowledge_document_chunks_fingerprint",
    }
    owner_key = constraints["fk_knowledge_document_chunks_document_owner"]
    assert isinstance(owner_key, ForeignKeyConstraint)
    assert tuple(item.target_fullname for item in owner_key.elements) == (
        "knowledge_documents.id",
        "knowledge_documents.user_id",
    )
    assert owner_key.ondelete == "CASCADE"
    uniqueness = constraints["uq_knowledge_document_chunks_document_ordinal"]
    assert isinstance(uniqueness, UniqueConstraint)
    assert tuple(item.name for item in uniqueness.columns) == (
        "document_id",
        "ordinal",
    )
    checks = {
        item.name: str(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "2000" in checks["ck_knowledge_document_chunks_content_length"]
    assert "199" in checks["ck_knowledge_document_chunks_ordinal"]
    assert {item.name for item in table.indexes} == {
        "ix_knowledge_document_chunks_owner_document",
        "ix_knowledge_document_chunks_search_vector",
        "ix_knowledge_document_chunks_embedding_hnsw",
    }
