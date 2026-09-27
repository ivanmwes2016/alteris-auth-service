"""drop unique constraint on tenants.plan

Revision ID: a67db58763cb
Revises: 24f0cfab220d
Create Date: 2026-09-27 20:55:41.525248

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a67db58763cb"
down_revision: str | None = "24f0cfab220d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tenants_plan", table_name="tenants")
    op.create_index("ix_tenants_plan", "tenants", ["plan"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tenants_plan", table_name="tenants")
    op.create_index("ix_tenants_plan", "tenants", ["plan"], unique=True)
