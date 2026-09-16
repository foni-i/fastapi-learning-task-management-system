"""Create owner-scoped parsed knowledge documents.

Revision ID: d7a1e4c9b320
Revises: c4d8a1f6e205
Create Date: 2026-09-06 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7a1e4c9b320"
down_revision: str | Sequence[str] | None = "c4d8a1f6e205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create private parsed-document storage and owner lookup indexes."""

    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'PARSED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "byte_count BETWEEN 1 AND 5242880",
            name="ck_knowledge_documents_byte_count",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_documents_content_sha256",
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> ''",
            name="ck_knowledge_documents_display_name_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(extracted_text) BETWEEN 1 AND 200000",
            name="ck_knowledge_documents_extracted_text_length",
        ),
        sa.CheckConstraint(
            "media_type IN ('text/plain', 'text/markdown', 'application/pdf')",
            name="ck_knowledge_documents_media_type",
        ),
        sa.CheckConstraint(
            "page_count BETWEEN 1 AND 100",
            name="ck_knowledge_documents_page_count",
        ),
        sa.CheckConstraint(
            "status = 'PARSED'",
            name="ck_knowledge_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_knowledge_documents_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
        sa.UniqueConstraint("id", "user_id", name="uq_knowledge_documents_id_user_id"),
    )
    op.create_index(
        "ix_knowledge_documents_user_id",
        "knowledge_documents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_user_status",
        "knowledge_documents",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only Task 11.1 document storage."""

    op.drop_index(
        "ix_knowledge_documents_user_status",
        table_name="knowledge_documents",
    )
    op.drop_index(
        "ix_knowledge_documents_user_id",
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
