"""Private owner-scoped chunks for lexical and vector retrieval."""

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.agent.embeddings import EMBEDDING_DIMENSIONS
from app.db.base import Base


class KnowledgeDocumentChunk(Base):
    """Persist one private deterministic chunk and its fixed-size embedding."""

    __tablename__ = "knowledge_document_chunks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_knowledge_document_chunks"),
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["knowledge_documents.id", "knowledge_documents.user_id"],
            name="fk_knowledge_document_chunks_document_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_knowledge_document_chunks_document_ordinal",
        ),
        CheckConstraint(
            "page_number BETWEEN 1 AND 100",
            name="ck_knowledge_document_chunks_page_number",
        ),
        CheckConstraint(
            "ordinal BETWEEN 0 AND 199",
            name="ck_knowledge_document_chunks_ordinal",
        ),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 2000",
            name="ck_knowledge_document_chunks_content_length",
        ),
        CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_document_chunks_fingerprint",
        ),
        Index(
            "ix_knowledge_document_chunks_owner_document",
            "user_id",
            "document_id",
        ),
        Index(
            "ix_knowledge_document_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_knowledge_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=False
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content)", persisted=True),
        nullable=False,
    )
