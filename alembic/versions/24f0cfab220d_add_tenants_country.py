"""add tenants.country

Revision ID: 24f0cfab220d
Revises: d7a3f1b09c55
Create Date: 2026-09-23 21:24:31.664796

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24f0cfab220d"
down_revision: str | None = "d7a3f1b09c55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("country", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "country")
