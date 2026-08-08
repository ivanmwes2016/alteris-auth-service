"""

Revision ID: 6ff9a5818d89
Revises: 11b50f498f57
Create Date: 2026-07-04 22:32:06.338262

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6ff9a5818d89"
down_revision: str | None = "11b50f498f57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
