"""Owner-scoped chunk persistence with caller-owned transactions."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_document_chunk import KnowledgeDocumentChunk


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentChunkCreate:
    """Carry one Service-validated chunk into persistence."""

    page_number: int
    ordinal: int
    content: str
    content_fingerprint: str
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentChunkMatch:
    """Carry private query output to the Service for public projection."""

    chunk_id: UUID
    document_id: UUID
    source: str
    page_number: int | None
    ordinal: int
    content: str
    distance: float


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentLexicalMatch:
    """Carry one owner-filtered lexical candidate to the Service."""

    chunk_id: UUID
    document_id: UUID
    source: str
    page_number: int | None
    ordinal: int
    content: str
    score: float


class KnowledgeDocumentChunkRepository:
    """Replace private chunks through one supplied synchronous Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_owned(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: Sequence[KnowledgeDocumentChunkCreate],
    ) -> list[KnowledgeDocumentChunk]:
        """Delete and recreate one owner's document chunks without committing."""

        self._session.execute(
            delete(KnowledgeDocumentChunk).where(
                KnowledgeDocumentChunk.document_id == document_id,
                KnowledgeDocumentChunk.user_id == user_id,
            )
        )
        models = [
            KnowledgeDocumentChunk(
                document_id=document_id,
                user_id=user_id,
                page_number=chunk.page_number,
                ordinal=chunk.ordinal,
                content=chunk.content,
                content_fingerprint=chunk.content_fingerprint,
                embedding=list(chunk.embedding),
            )
            for chunk in chunks
        ]
        self._session.add_all(models)
        self._session.flush()
        return models

    def search_vector_owned(
        self,
        *,
        user_id: UUID,
        query_embedding: Sequence[float],
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> tuple[KnowledgeDocumentChunkMatch, ...]:
        """Filter by trusted ownership before bounded cosine-distance ranking."""

        distance = KnowledgeDocumentChunk.embedding.cosine_distance(
            list(query_embedding)
        ).label("distance")
        statement = (
            select(
                KnowledgeDocumentChunk.id,
                KnowledgeDocumentChunk.document_id,
                KnowledgeDocument.display_name,
                KnowledgeDocumentChunk.page_number,
                KnowledgeDocumentChunk.ordinal,
                KnowledgeDocumentChunk.content,
                distance,
            )
            .join(
                KnowledgeDocument,
                and_(
                    KnowledgeDocument.id == KnowledgeDocumentChunk.document_id,
                    KnowledgeDocument.user_id == KnowledgeDocumentChunk.user_id,
                ),
            )
            .where(
                KnowledgeDocumentChunk.user_id == user_id,
                KnowledgeDocument.user_id == user_id,
            )
        )
        if document_ids is not None:
            statement = statement.where(
                KnowledgeDocumentChunk.document_id.in_(document_ids)
            )
        statement = statement.order_by(
            distance.asc(),
            KnowledgeDocumentChunk.id.asc(),
        ).limit(top_k)

        rows = self._session.execute(statement).tuples().all()
        return tuple(
            KnowledgeDocumentChunkMatch(
                chunk_id=chunk_id,
                document_id=document_id,
                source=source,
                page_number=page_number,
                ordinal=ordinal,
                content=content,
                distance=float(row_distance),
            )
            for (
                chunk_id,
                document_id,
                source,
                page_number,
                ordinal,
                content,
                row_distance,
            ) in rows
        )

    def search_lexical_owned(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> tuple[KnowledgeDocumentLexicalMatch, ...]:
        """Filter by trusted ownership before bounded simple-text ranking."""

        tsquery = func.plainto_tsquery("simple", query)
        score = func.ts_rank_cd(
            KnowledgeDocumentChunk.search_vector,
            tsquery,
        ).label("lexical_score")
        statement = (
            select(
                KnowledgeDocumentChunk.id,
                KnowledgeDocumentChunk.document_id,
                KnowledgeDocument.display_name,
                KnowledgeDocumentChunk.page_number,
                KnowledgeDocumentChunk.ordinal,
                KnowledgeDocumentChunk.content,
                score,
            )
            .join(
                KnowledgeDocument,
                and_(
                    KnowledgeDocument.id == KnowledgeDocumentChunk.document_id,
                    KnowledgeDocument.user_id == KnowledgeDocumentChunk.user_id,
                ),
            )
            .where(
                KnowledgeDocumentChunk.user_id == user_id,
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocumentChunk.search_vector.op("@@")(tsquery),
            )
        )
        if document_ids is not None:
            statement = statement.where(
                KnowledgeDocumentChunk.document_id.in_(document_ids)
            )
        statement = statement.order_by(
            score.desc(),
            KnowledgeDocumentChunk.id.asc(),
        ).limit(top_k)

        rows = self._session.execute(statement).tuples().all()
        return tuple(
            KnowledgeDocumentLexicalMatch(
                chunk_id=chunk_id,
                document_id=document_id,
                source=source,
                page_number=page_number,
                ordinal=ordinal,
                content=content,
                score=float(row_score),
            )
            for (
                chunk_id,
                document_id,
                source,
                page_number,
                ordinal,
                content,
                row_score,
            ) in rows
        )
