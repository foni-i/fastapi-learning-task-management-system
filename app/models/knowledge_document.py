"""Owner-scoped parsed document storage for focused RAG."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeDocumentStatus(StrEnum):
    """Persisted lifecycle values for parsed and explicitly indexed documents."""

    PARSED = "PARSED"
    INDEXED = "INDEXED"


class KnowledgeDocument(Base):
    """Persist private extracted text with trusted owner identity."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
        UniqueConstraint("id", "user_id", name="uq_knowledge_documents_id_user_id"),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_knowledge_documents_user_id_users",
        ),
        CheckConstraint(
            "btrim(display_name) <> ''",
            name="ck_knowledge_documents_display_name_not_blank",
        ),
        CheckConstraint(
            "media_type IN ('text/plain', 'text/markdown', 'application/pdf')",
            name="ck_knowledge_documents_media_type",
        ),
        CheckConstraint(
            "byte_count BETWEEN 1 AND 5242880",
            name="ck_knowledge_documents_byte_count",
        ),
        CheckConstraint(
            "page_count BETWEEN 1 AND 100",
            name="ck_knowledge_documents_page_count",
        ),
        CheckConstraint(
            "char_length(extracted_text) BETWEEN 1 AND 200000",
            name="ck_knowledge_documents_extracted_text_length",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_documents_content_sha256",
        ),
        CheckConstraint(
            "status IN ('PARSED', 'INDEXED')",
            name="ck_knowledge_documents_status",
        ),
        Index("ix_knowledge_documents_user_id", "user_id"),
        Index("ix_knowledge_documents_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PARSED'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
