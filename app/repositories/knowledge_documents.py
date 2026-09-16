"""Owner-scoped document persistence with caller-owned transactions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_document import KnowledgeDocument, KnowledgeDocumentStatus


class KnowledgeDocumentRepository:
    """Persist and query documents through one supplied synchronous Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: UUID,
        display_name: str,
        media_type: str,
        byte_count: int,
        content_sha256: str,
        page_count: int,
        extracted_text: str,
    ) -> KnowledgeDocument:
        """Add one parsed document and flush without committing."""

        document = KnowledgeDocument(
            user_id=user_id,
            display_name=display_name,
            media_type=media_type,
            byte_count=byte_count,
            content_sha256=content_sha256,
            page_count=page_count,
            extracted_text=extracted_text,
        )
        self._session.add(document)
        self._session.flush()
        return document

    def get_owned_by_id(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> KnowledgeDocument | None:
        """Return a document only when resource and owner IDs match."""

        statement = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.user_id == user_id,
        )
        return self._session.scalar(statement)

    def mark_indexed(
        self,
        document: KnowledgeDocument,
        *,
        updated_at: datetime,
    ) -> KnowledgeDocument:
        """Mark one already owner-resolved document and flush without committing."""

        document.status = KnowledgeDocumentStatus.INDEXED.value
        document.updated_at = updated_at
        self._session.flush()
        return document
