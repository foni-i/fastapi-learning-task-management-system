"""Create private refresh-token digest storage.

Revision ID: 86cd95365562
Revises: e3b7c2d9a410
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "86cd95365562"
down_revision: str | Sequence[str] | None = "e3b7c2d9a410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add only the digest table, its integrity constraints and owner index."""

    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users"
        ),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_tokens_token_hash_format",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expiry_after_creation",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_refresh_tokens_revocation_not_before_creation",
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])


def downgrade() -> None:
    """Remove this table only; stored refresh credentials cannot be recovered."""

    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
