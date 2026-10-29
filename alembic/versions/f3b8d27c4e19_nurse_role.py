"""nurse role: a team role that edits students' medical records

Revision ID: f3b8d27c4e19
Revises: e7a41c9d2b56
Create Date: 2026-10-29 16:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b8d27c4e19"
down_revision: str | None = "e7a41c9d2b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO roles (id, name, description) VALUES (:id, 'nurse', 'Looks after students'' medical records') "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"id": uuid.uuid4()},
    )


def downgrade() -> None:
    # Fails on the roles FK (rather than destroying data) if any member is still a nurse.
    op.get_bind().execute(sa.text("DELETE FROM roles WHERE name = 'nurse'"))
