"""Private refresh-token storage without issuance or authentication behavior."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefreshToken(Base):
    """Store only a credential digest and its owner/lifetime metadata."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users"
        ),
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_tokens_token_hash_format",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expiry_after_creation",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_refresh_tokens_revocation_not_before_creation",
        ),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
