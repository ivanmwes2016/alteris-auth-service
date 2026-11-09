"""student cards: NFC cards linked to students, and how each mark was taken

Revision ID: d3f7a2c81e45
Revises: c8e2f4a61d07
Create Date: 2026-11-09 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3f7a2c81e45"
down_revision: str | None = "c8e2f4a61d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "student_cards",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("linked_by", sa.UUID(), nullable=True),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["linked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id"),
        sa.UniqueConstraint("tenant_id", "uid", name="uq_student_card_tenant_uid"),
    )
    op.add_column(
        "attendance_mark_changes",
        sa.Column("source", sa.String(), server_default="manual", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("attendance_mark_changes", "source")
    op.drop_table("student_cards")
