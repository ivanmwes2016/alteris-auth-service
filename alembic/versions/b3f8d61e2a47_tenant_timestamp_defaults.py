"""default created_at/updated_at on tenants, and fill in the missing ones

The model has server_default=now() but the production table never got it, so schools
were saved without a registration time. Missing times become the owner's account
creation time, which is when they signed up; failing that, now.

Revision ID: b3f8d61e2a47
Revises: 9e4b2f7a1c58
Create Date: 2026-10-22 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f8d61e2a47"
down_revision: str | None = "9e4b2f7a1c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column("tenants", column, server_default=sa.func.now())

    op.execute(
        """
        UPDATE tenants
        SET created_at = COALESCE(
            (SELECT users.created_at FROM users WHERE users.id = tenants.owner_id), now()
        )
        WHERE created_at IS NULL
        """
    )
    op.execute("UPDATE tenants SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    # The filled-in times stay; only the defaults go.
    for column in ("created_at", "updated_at"):
        op.alter_column("tenants", column, server_default=None)
