import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.config import get_settings
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember

log = logging.getLogger(__name__)
config = get_settings()


def trial_expired(tenant: Tenant) -> bool:
    """Approved, past the end of its free trial, and still on the free plan."""
    return (
        tenant.status == "approved"
        and tenant.trial_ends_at is not None
        and tenant.trial_ends_at <= datetime.now(UTC)
        and (tenant.plan or "free") == "free"
    )


async def get_user_context(db: AsyncSession, user_id: str, supabase: Client) -> dict[str, Any]:
    try:
        result = await db.execute(
            select(TenantMember, Tenant, Role)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .join(Role, Role.id == TenantMember.role_id)
            .where(TenantMember.user_id == user_id, TenantMember.status == "active")
        )

        row = result.first()

        if not row:
            return {
                "tenant": None,
                "role": None,
                "subscription_active": False,
            }

        _, tenant, role = row

        logo_url = ""

        if tenant.logo_path:
            signed = supabase.storage.from_(config.SUPABASE_LOGO_BUCKET_NAME).create_signed_url(
                tenant.logo_path,
                60 * 60,  # 1 hour
            )
            logo_url = signed["signedURL"]

        return {
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "workspace_id": tenant.workspace_id,
                "logo_path": logo_url,
                "plan": tenant.plan,
                "status": tenant.status,
                "trial_ends_at": tenant.trial_ends_at,
                "trial_expired": trial_expired(tenant),
            },
            "role": role.name,
            "subscription_active": True,  # To Do ==> get from stripe subscription
        }

    except SQLAlchemyError as exc:
        log.exception("Failed to load user context")
        await db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Failed to load user context",
        ) from exc
