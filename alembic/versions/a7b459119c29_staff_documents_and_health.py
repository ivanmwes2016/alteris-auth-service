"""staff documents and health: the Documents and Health tabs on a staff profile

Revision ID: a7b459119c29
Revises: 4fafcc2dc48a
Create Date: 2026-10-30 12:37:56.471141

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b459119c29"
down_revision: str | None = "4fafcc2dc48a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("staff_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_documents_staff_id", "staff_documents", ["staff_id"], unique=False)
    op.create_index(
        op.f("ix_staff_documents_tenant_id"), "staff_documents", ["tenant_id"], unique=False
    )
    op.create_table(
        "staff_health_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("staff_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("emergency_contact", sa.Text(), nullable=True),
        sa.Column("emergency_phone", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("staff_id"),
    )
    op.create_index(
        "ix_staff_health_profiles_tenant_id", "staff_health_profiles", ["tenant_id"], unique=False
    )
    op.create_table(
        "staff_allergies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("reaction", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["staff_health_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_staff_allergies_profile_id", "staff_allergies", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_staff_allergies_tenant_id"), "staff_allergies", ["tenant_id"], unique=False
    )
    op.create_table(
        "staff_health_conditions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["staff_health_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_staff_health_conditions_profile_id",
        "staff_health_conditions",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_staff_health_conditions_tenant_id"),
        "staff_health_conditions",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_staff_health_conditions_tenant_id"), table_name="staff_health_conditions"
    )
    op.drop_index("ix_staff_health_conditions_profile_id", table_name="staff_health_conditions")
    op.drop_table("staff_health_conditions")
    op.drop_index(op.f("ix_staff_allergies_tenant_id"), table_name="staff_allergies")
    op.drop_index("ix_staff_allergies_profile_id", table_name="staff_allergies")
    op.drop_table("staff_allergies")
    op.drop_index("ix_staff_health_profiles_tenant_id", table_name="staff_health_profiles")
    op.drop_table("staff_health_profiles")
    op.drop_index(op.f("ix_staff_documents_tenant_id"), table_name="staff_documents")
    op.drop_index("ix_staff_documents_staff_id", table_name="staff_documents")
    op.drop_table("staff_documents")
