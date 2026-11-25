"""parent app access: parents linked to a login, their invites, push settings, and
term marks published to parents

Revision ID: a6c4e2f19b83
Revises: d3f7a2c81e45
Create Date: 2026-11-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c4e2f19b83"
down_revision: str | None = "d3f7a2c81e45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("parents", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("parents", sa.Column("invite_token_hash", sa.String(), nullable=True))
    op.add_column(
        "parents", sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("parents", sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("parents", sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_parents_user_id_users", "parents", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_parents_user_id", "parents", ["user_id"])
    op.create_unique_constraint(
        "uq_parents_invite_token_hash", "parents", ["invite_token_hash"]
    )

    op.create_table(
        "parent_push_settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("attendance", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("results", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("health", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("behaviour", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "performance_releases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("class_name", sa.Text(), nullable=False),
        sa.Column("term_label", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("released_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "class_name", "term_label", "year", name="uq_performance_release"
        ),
    )


def downgrade() -> None:
    op.drop_table("performance_releases")
    op.drop_table("parent_push_settings")
    op.drop_constraint("uq_parents_invite_token_hash", "parents", type_="unique")
    op.drop_index("ix_parents_user_id", table_name="parents")
    op.drop_constraint("fk_parents_user_id_users", "parents", type_="foreignkey")
    op.drop_column("parents", "joined_at")
    op.drop_column("parents", "invited_at")
    op.drop_column("parents", "invite_expires_at")
    op.drop_column("parents", "invite_token_hash")
    op.drop_column("parents", "user_id")
