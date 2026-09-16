"""Unit contracts for owner-scoped atomic document indexing."""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.embeddings import EMBEDDING_DIMENSIONS
from app.core.exceptions import (
    KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
    KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    KnowledgeDocumentIndexingError,
    KnowledgeDocumentNotFoundError,
)
from app.models import KnowledgeDocument, KnowledgeDocumentStatus
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkCreate,
    KnowledgeDocumentChunkRepository,
)
from app.services.knowledge_document_indexing import index_knowledge_document
from tests.fakes.embedding_provider import DeterministicEmbeddingProvider


class FakeDocumentRepository:
    def __init__(self, document: KnowledgeDocument | None) -> None:
        self.document = document
        self.lookups: list[tuple[UUID, UUID]] = []
        self.marked_at: datetime | None = None

    def get_owned_by_id(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> KnowledgeDocument | None:
        self.lookups.append((document_id, user_id))
        return self.document

    def mark_indexed(
        self,
        document: KnowledgeDocument,
        *,
        updated_at: datetime,
    ) -> KnowledgeDocument:
        self.marked_at = updated_at
        document.status = KnowledgeDocumentStatus.INDEXED.value
        document.updated_at = updated_at
        return document


class FakeChunkRepository:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[
            tuple[UUID, UUID, tuple[KnowledgeDocumentChunkCreate, ...]]
        ] = []

    def replace_owned(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: Sequence[KnowledgeDocumentChunkCreate],
    ) -> list[object]:
        values = tuple(chunks)
        self.calls.append((document_id, user_id, values))
        if self.failure is not None:
            raise self.failure
        return [object() for _ in values]


def _document(*, text: str = "a" * 2001) -> KnowledgeDocument:
    now = datetime.now(UTC)
    return cast(
        KnowledgeDocument,
        SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            display_name="notes.txt",
            media_type="text/plain",
            byte_count=len(text.encode()),
            content_sha256="a" * 64,
            page_count=1,
            extracted_text=text,
            status=KnowledgeDocumentStatus.PARSED.value,
            created_at=now,
            updated_at=now,
        ),
    )


def test_indexing_derives_owner_chunks_once_and_commits_atomic_result() -> None:
    document = _document()
    session = MagicMock(spec=Session)
    documents = FakeDocumentRepository(document)
    chunks = FakeChunkRepository()
    provider = DeterministicEmbeddingProvider()
    indexed_at = datetime(2026, 9, 6, tzinfo=UTC)

    result = index_knowledge_document(
        document.id,
        document.user_id,
        session,
        embedding_provider=provider,
        timeout_seconds=4,
        document_repository_factory=lambda _session: documents,
        chunk_repository_factory=lambda _session: chunks,
        clock=lambda: indexed_at,
    )

    assert result.chunk_count == 2
    assert result.document.status is KnowledgeDocumentStatus.INDEXED
    assert documents.lookups == [(document.id, document.user_id)]
    assert documents.marked_at == indexed_at
    assert len(provider.calls) == 1
    assert provider.calls[0].input_count == 2
    stored_document_id, stored_user_id, stored_chunks = chunks.calls[0]
    assert (stored_document_id, stored_user_id) == (document.id, document.user_id)
    assert [item.ordinal for item in stored_chunks] == [0, 1]
    assert all(len(item.embedding) == EMBEDDING_DIMENSIONS for item in stored_chunks)
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_indexing_hides_missing_or_foreign_document_before_provider_call() -> None:
    session = MagicMock(spec=Session)
    documents = FakeDocumentRepository(None)
    chunks = FakeChunkRepository()
    provider = DeterministicEmbeddingProvider()
    document_id = uuid4()
    user_id = uuid4()

    with pytest.raises(
        KnowledgeDocumentNotFoundError,
        match=KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    ):
        index_knowledge_document(
            document_id,
            user_id,
            session,
            embedding_provider=provider,
            timeout_seconds=1,
            document_repository_factory=lambda _session: documents,
            chunk_repository_factory=lambda _session: chunks,
        )

    assert documents.lookups == [(document_id, user_id)]
    assert provider.calls == []
    assert chunks.calls == []
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


@pytest.mark.parametrize("invalid", ([0.0] * 1535, [float("nan")] * 1536))
def test_service_revalidates_provider_vectors_before_persistence(
    invalid: list[float],
) -> None:
    document = _document(text="private material")
    session = MagicMock(spec=Session)
    documents = FakeDocumentRepository(document)
    chunks = FakeChunkRepository()

    class InvalidProvider:
        def embed(
            self,
            _texts: Sequence[str],
            *,
            timeout_seconds: float,
        ) -> Sequence[Sequence[float]]:
            assert timeout_seconds == 1
            return (invalid,)

    with pytest.raises(
        KnowledgeDocumentIndexingError,
        match=KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
    ):
        index_knowledge_document(
            document.id,
            document.user_id,
            session,
            embedding_provider=InvalidProvider(),
            timeout_seconds=1,
            document_repository_factory=lambda _session: documents,
            chunk_repository_factory=lambda _session: chunks,
        )

    assert chunks.calls == []
    assert documents.marked_at is None
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_persistence_failure_rolls_back_without_marking_document() -> None:
    document = _document(text="private material")
    session = MagicMock(spec=Session)
    documents = FakeDocumentRepository(document)
    chunks = FakeChunkRepository(failure=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        index_knowledge_document(
            document.id,
            document.user_id,
            session,
            embedding_provider=DeterministicEmbeddingProvider(),
            timeout_seconds=1,
            document_repository_factory=lambda _session: documents,
            chunk_repository_factory=lambda _session: chunks,
        )

    assert documents.marked_at is None
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_chunk_repository_flushes_but_never_commits() -> None:
    session = MagicMock(spec=Session)
    repository = KnowledgeDocumentChunkRepository(session)

    models = repository.replace_owned(
        document_id=uuid4(),
        user_id=uuid4(),
        chunks=(
            KnowledgeDocumentChunkCreate(
                page_number=1,
                ordinal=0,
                content="private chunk",
                content_fingerprint="a" * 64,
                embedding=tuple([0.1] * EMBEDDING_DIMENSIONS),
            ),
        ),
    )

    assert len(models) == 1
    session.execute.assert_called_once()
    session.add_all.assert_called_once_with(models)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
