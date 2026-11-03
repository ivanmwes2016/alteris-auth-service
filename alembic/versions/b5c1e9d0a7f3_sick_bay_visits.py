"""sick bay visits: students sent to the nurse and what happened

Revision ID: b5c1e9d0a7f3
Revises: a7b459119c29
Create Date: 2026-10-30 23:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c1e9d0a7f3"
down_revision: str | None = "a7b459119c29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sick_bay_visits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("complaint", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sent_by_name", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("treatment", sa.Text(), nullable=True),
        sa.Column("nurse_notes", sa.Text(), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sick_bay_visits_tenant_day", "sick_bay_visits", ["tenant_id", "day"], unique=False
    )
    op.create_index(
        "ix_sick_bay_visits_student_id", "sick_bay_visits", ["student_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sick_bay_visits_student_id", table_name="sick_bay_visits")
    op.drop_index("ix_sick_bay_visits_tenant_day", table_name="sick_bay_visits")
    op.drop_table("sick_bay_visits")
