"""Add the canonical user email uniqueness constraint.

Revision ID: 9f3b2d6e8a41
Revises: 7c9e1b4a6d32
Create Date: 2026-08-26 14:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3b2d6e8a41"
down_revision: str | Sequence[str] | None = "7c9e1b4a6d32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prevent duplicate canonical values stored in users.email."""

    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    """Remove only the canonical email uniqueness constraint."""

    op.drop_constraint("uq_users_email", "users", type_="unique")
