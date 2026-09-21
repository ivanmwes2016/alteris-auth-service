"""team management: tenant owner, member last-active, team roles, pending-invite uniqueness

Revision ID: c41d9e7a2b10
Revises: 7e1479cd7be6
Create Date: 2026-09-21 10:00:00.000000

"""

import logging
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = "c41d9e7a2b10"
down_revision: str | None = "7e1479cd7be6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `admin` and `teacher` already exist; these are the UI's remaining team roles.
NEW_ROLES = {
    "head_teacher": "Head Teacher",
    "admissions_officer": "Admissions Officer",
    "bursar": "Bursar",
}


def upgrade() -> None:
    op.add_column("tenants", sa.Column("owner_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_tenants_owner_id_users", "tenants", "users", ["owner_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "tenant_members", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Backfill the workspace creator, but only where it's unambiguous: a tenant with
    # exactly one admin. tenant_members.joined_at/created_at are NULL in practice (the
    # columns have no DB default), so "earliest admin" can't be trusted to pick the
    # creator when there are several — those tenants are left NULL rather than guessed.
    op.execute(
        """
        UPDATE tenants t SET owner_id = sole.user_id
        FROM (
            SELECT tm.tenant_id, MIN(tm.user_id::text)::uuid AS user_id
            FROM tenant_members tm
            JOIN roles r ON r.id = tm.role_id
            WHERE r.name = 'admin'
            GROUP BY tm.tenant_id
            HAVING COUNT(*) = 1
        ) sole
        WHERE t.id = sole.tenant_id AND t.owner_id IS NULL
        """
    )
    ambiguous = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT COUNT(*) FROM tenants t
            WHERE t.owner_id IS NULL AND EXISTS (
                SELECT 1 FROM tenant_members tm JOIN roles r ON r.id = tm.role_id
                WHERE tm.tenant_id = t.id AND r.name = 'admin'
            )
            """
            )
        )
        .scalar()
    )
    if ambiguous:
        log.warning(
            "%s tenant(s) have several admins and no recorded owner; set tenants.owner_id "
            "manually or nobody there can manage users.",
            ambiguous,
        )

    bind = op.get_bind()
    for name, description in NEW_ROLES.items():
        bind.execute(
            sa.text(
                "INSERT INTO roles (id, name, description) VALUES (:id, :name, :description) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "name": name, "description": description},
        )

    op.create_index(
        "uq_invites_tenant_email_pending",
        "invites",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_invites_tenant_email_pending",
        table_name="invites",
        postgresql_where=sa.text("accepted_at IS NULL"),
    )

    # Fails on the roles FK (rather than destroying data) if any member still holds one.
    op.get_bind().execute(
        sa.text("DELETE FROM roles WHERE name = ANY(:names)"), {"names": list(NEW_ROLES)}
    )

    op.drop_column("tenant_members", "last_active_at")
    op.drop_constraint("fk_tenants_owner_id_users", "tenants", type_="foreignkey")
    op.drop_column("tenants", "owner_id")
