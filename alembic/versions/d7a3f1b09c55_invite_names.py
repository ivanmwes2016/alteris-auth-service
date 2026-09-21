"""store the invitee's first and last name on invites

Revision ID: d7a3f1b09c55
Revises: c41d9e7a2b10
Create Date: 2026-09-21 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a3f1b09c55"
down_revision: str | None = "c41d9e7a2b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invites", sa.Column("first_name", sa.String(), nullable=True))
    op.add_column("invites", sa.Column("last_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("invites", "last_name")
    op.drop_column("invites", "first_name")
