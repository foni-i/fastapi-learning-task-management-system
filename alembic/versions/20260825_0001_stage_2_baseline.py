"""Create the empty Stage 2 baseline.

Revision ID: 2f6a8c1d4b90
Revises:
Create Date: 2026-08-25 20:45:00

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2f6a8c1d4b90"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish the initial migration boundary without product tables."""

    pass


def downgrade() -> None:
    """Return to the migration base without dropping product tables."""

    pass
