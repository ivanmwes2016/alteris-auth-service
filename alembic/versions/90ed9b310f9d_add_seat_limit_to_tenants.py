"""add seat_limit to tenants

Revision ID: 90ed9b310f9d
Revises: 8fdabbfbde24
Create Date: 2026-08-08 22:48:45.385490
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "90ed9b310f9d"
down_revision: Union[str, None] = "8fdabbfbde24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "seat_limit",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )

    op.add_column(
        "tenants",
        sa.Column(
            "stripe_customer_id",
            sa.String(),
            nullable=True,
        ),
    )

    op.add_column(
        "tenants",
        sa.Column(
            "stripe_subscription_id",
            sa.String(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "stripe_subscription_id")
    op.drop_column("tenants", "stripe_customer_id")
    op.drop_column("tenants", "seat_limit")