"""school approval and trial dates on tenants

Revision ID: 7c3d9a1e4b20
Revises: 5b8e2c4d9f13
Create Date: 2026-10-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3d9a1e4b20"
down_revision: str | None = "5b8e2c4d9f13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing schools are already in use, so they start out approved.
    op.add_column(
        "tenants",
        sa.Column("status", sa.String(), nullable=False, server_default="approved"),
    )
    op.add_column("tenants", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("review_token_hash", sa.String(), nullable=True))
    op.create_unique_constraint("tenants_review_token_hash_key", "tenants", ["review_token_hash"])


def downgrade() -> None:
    op.drop_constraint("tenants_review_token_hash_key", "tenants", type_="unique")
    op.drop_column("tenants", "review_token_hash")
    op.drop_column("tenants", "trial_ends_at")
    op.drop_column("tenants", "approved_at")
    op.drop_column("tenants", "status")
