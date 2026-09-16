"""Deterministic chunking and atomic owner-scoped document indexing."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    validate_embedding_batch,
)
from app.core.exceptions import (
    KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
    KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    KnowledgeDocumentIndexingError,
    KnowledgeDocumentNotFoundError,
)
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkCreate,
    KnowledgeDocumentChunkRepository,
)
from app.repositories.knowledge_documents import KnowledgeDocumentRepository
from app.schemas.knowledge_document import (
    KnowledgeDocumentIndexResponse,
    PublicKnowledgeDocument,
)
from app.services.knowledge_documents import DOCUMENT_PAGE_SEPARATOR

MAX_CHUNK_CHARACTERS = 2_000
CHUNK_OVERLAP_CHARACTERS = 200
MAX_DOCUMENT_CHUNKS = 200
CHUNK_STEP_CHARACTERS = MAX_CHUNK_CHARACTERS - CHUNK_OVERLAP_CHARACTERS


class DocumentRepositoryPort(Protocol):
    def get_owned_by_id(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> KnowledgeDocument | None: ...

    def mark_indexed(
        self,
        document: KnowledgeDocument,
        *,
        updated_at: datetime,
    ) -> KnowledgeDocument: ...


class ChunkRepositoryPort(Protocol):
    def replace_owned(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: Sequence[KnowledgeDocumentChunkCreate],
    ) -> Sequence[object]: ...


DocumentRepositoryFactory = Callable[[Session], DocumentRepositoryPort]
ChunkRepositoryFactory = Callable[[Session], ChunkRepositoryPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class DeterministicDocumentChunk:
    """Carry stable page, order, private content, and fingerprint values."""

    page_number: int
    ordinal: int
    content: str
    content_fingerprint: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def _page_texts(extracted_text: str, page_count: int) -> tuple[str, ...]:
    if page_count == 1:
        return (extracted_text,)
    pages = tuple(extracted_text.split(DOCUMENT_PAGE_SEPARATOR))
    if len(pages) != page_count:
        raise KnowledgeDocumentIndexingError(KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE)
    return pages


def chunk_document_text(
    extracted_text: str,
    *,
    page_count: int,
) -> tuple[DeterministicDocumentChunk, ...]:
    """Split normalized text deterministically without crossing page boundaries."""

    chunks: list[DeterministicDocumentChunk] = []
    for page_number, page_text in enumerate(
        _page_texts(extracted_text, page_count), start=1
    ):
        if not page_text:
            continue
        start = 0
        while start < len(page_text):
            content = page_text[start : start + MAX_CHUNK_CHARACTERS]
            ordinal = len(chunks)
            fingerprint = sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                DeterministicDocumentChunk(
                    page_number=page_number,
                    ordinal=ordinal,
                    content=content,
                    content_fingerprint=fingerprint,
                )
            )
            if len(chunks) > MAX_DOCUMENT_CHUNKS:
                raise KnowledgeDocumentIndexingError(
                    KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE
                )
            if start + MAX_CHUNK_CHARACTERS >= len(page_text):
                break
            start += CHUNK_STEP_CHARACTERS
    if not chunks:
        raise KnowledgeDocumentIndexingError(KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE)
    return tuple(chunks)


def index_knowledge_document(
    document_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    embedding_provider: EmbeddingProvider,
    timeout_seconds: float,
    document_repository_factory: DocumentRepositoryFactory = (
        KnowledgeDocumentRepository
    ),
    chunk_repository_factory: ChunkRepositoryFactory = (
        KnowledgeDocumentChunkRepository
    ),
    clock: Clock = utc_now,
) -> KnowledgeDocumentIndexResponse:
    """Replace one owned document's chunks and status in a single transaction."""

    try:
        document_repository = document_repository_factory(session)
        document = document_repository.get_owned_by_id(
            document_id=document_id,
            user_id=user_id,
        )
        if document is None:
            raise KnowledgeDocumentNotFoundError(KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE)

        chunks = chunk_document_text(
            document.extracted_text,
            page_count=document.page_count,
        )
        raw_vectors = embedding_provider.embed(
            tuple(chunk.content for chunk in chunks),
            timeout_seconds=timeout_seconds,
        )
        vectors = validate_embedding_batch(raw_vectors, expected_count=len(chunks))
        create_values = tuple(
            KnowledgeDocumentChunkCreate(
                page_number=chunk.page_number,
                ordinal=chunk.ordinal,
                content=chunk.content,
                content_fingerprint=chunk.content_fingerprint,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        stored = chunk_repository_factory(session).replace_owned(
            document_id=document.id,
            user_id=user_id,
            chunks=create_values,
        )
        document_repository.mark_indexed(document, updated_at=clock())
        result = KnowledgeDocumentIndexResponse(
            document=PublicKnowledgeDocument.model_validate(document),
            chunk_count=len(stored),
        )
        session.commit()
        return result
    except KnowledgeDocumentNotFoundError:
        session.rollback()
        raise
    except EmbeddingProviderError, KnowledgeDocumentIndexingError:
        session.rollback()
        raise KnowledgeDocumentIndexingError(
            KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE
        ) from None
    except Exception:
        session.rollback()
        raise
