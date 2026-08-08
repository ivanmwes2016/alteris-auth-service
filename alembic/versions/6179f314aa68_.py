"""

Revision ID: 6179f314aa68
Revises: decae5a53467
Create Date: 2026-07-14 20:41:50.438928

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6179f314aa68"
down_revision: str | None = "decae5a53467"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
