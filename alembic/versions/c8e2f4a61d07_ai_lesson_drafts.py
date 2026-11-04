"""ai lesson drafts: per-school settings, a draft log, and plans marked AI-assisted

Revision ID: c8e2f4a61d07
Revises: b5c1e9d0a7f3
Create Date: 2026-11-04 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2f4a61d07"
down_revision: str | None = "b5c1e9d0a7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lesson_plans",
        sa.Column("ai_assisted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "planning_ai_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("curriculum", sa.Text(), nullable=True),
        sa.Column("class_size", sa.SmallInteger(), nullable=True),
        sa.Column("has_power", sa.Boolean(), nullable=True),
        sa.Column("has_projector", sa.Boolean(), nullable=True),
        sa.Column("textbooks", sa.String(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "lesson_drafts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_drafts_tenant_created", "lesson_drafts", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_lesson_drafts_tenant_created", table_name="lesson_drafts")
    op.drop_table("lesson_drafts")
    op.drop_table("planning_ai_settings")
    op.drop_column("lesson_plans", "ai_assisted")
