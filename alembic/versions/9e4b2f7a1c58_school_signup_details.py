"""phone, school type and referral source on tenants

Revision ID: 9e4b2f7a1c58
Revises: 7c3d9a1e4b20
Create Date: 2026-10-22 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e4b2f7a1c58"
down_revision: str | None = "7c3d9a1e4b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("phone", sa.String(), nullable=True))
    op.add_column("tenants", sa.Column("school_type", sa.String(), nullable=True))
    op.add_column("tenants", sa.Column("referral_source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "referral_source")
    op.drop_column("tenants", "school_type")
    op.drop_column("tenants", "phone")
