"""Strict public metadata for private uploaded documents."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.knowledge_document import KnowledgeDocumentStatus

DOCUMENT_TIMESTAMP_MESSAGE = "Timestamp must include timezone information"


class PublicKnowledgeDocument(BaseModel):
    """Expose document metadata without owner ID, fingerprint, or extracted text."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        hide_input_in_errors=True,
    )

    id: UUID
    display_name: str
    media_type: str
    byte_count: int
    page_count: int
    status: KnowledgeDocumentStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(DOCUMENT_TIMESTAMP_MESSAGE)
        return value.astimezone(UTC)


class KnowledgeDocumentErrorResponse(BaseModel):
    """Document one fixed route-local safe error body."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class KnowledgeDocumentIndexResponse(BaseModel):
    """Return safe indexed metadata and the bounded number of stored chunks."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    document: PublicKnowledgeDocument
    chunk_count: int = Field(ge=1, le=200)
