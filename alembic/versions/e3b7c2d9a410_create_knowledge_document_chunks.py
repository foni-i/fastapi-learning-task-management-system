"""Create deterministic knowledge-document chunks with pgvector storage.

Revision ID: e3b7c2d9a410
Revises: d7a1e4c9b320
Create Date: 2026-09-06 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3b7c2d9a410"
down_revision: str | Sequence[str] | None = "d7a1e4c9b320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable pgvector and create private owner-scoped document chunks."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.drop_constraint(
        "ck_knowledge_documents_status",
        "knowledge_documents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_knowledge_documents_status",
        "knowledge_documents",
        "status IN ('PARSED', 'INDEXED')",
    )
    op.create_table(
        "knowledge_document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 2000",
            name="ck_knowledge_document_chunks_content_length",
        ),
        sa.CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_document_chunks_fingerprint",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 199",
            name="ck_knowledge_document_chunks_ordinal",
        ),
        sa.CheckConstraint(
            "page_number BETWEEN 1 AND 100",
            name="ck_knowledge_document_chunks_page_number",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["knowledge_documents.id", "knowledge_documents.user_id"],
            name="fk_knowledge_document_chunks_document_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document_chunks"),
        sa.UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_knowledge_document_chunks_document_ordinal",
        ),
    )
    op.create_index(
        "ix_knowledge_document_chunks_owner_document",
        "knowledge_document_chunks",
        ["user_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_chunks_search_vector",
        "knowledge_document_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_document_chunks_embedding_hnsw",
        "knowledge_document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Remove Task 11.2 storage while retaining parsed documents."""

    op.drop_table("knowledge_document_chunks")
    op.execute(
        "UPDATE knowledge_documents SET status = 'PARSED' WHERE status = 'INDEXED'"
    )
    op.drop_constraint(
        "ck_knowledge_documents_status",
        "knowledge_documents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_knowledge_documents_status",
        "knowledge_documents",
        "status = 'PARSED'",
    )
    op.execute("DROP EXTENSION IF EXISTS vector")
