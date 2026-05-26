from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.tenant_member import TenantMember
from app.db.models.tenant import Tenant


async def get_user_context(
    db: AsyncSession,
    user_id: str,
):
    result = await db.execute(
        select(TenantMember, Tenant)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.user_id == user_id)
    )

    row = result.first()

    if not row:
        return {
            "tenant": None,
            "role": None,
            "subscription_active": False,
        }

    membership, tenant = row

    return {
        "tenant": {
            "id": str(tenant.id),
            "name": tenant.name,
            "plan": tenant.plan,
        },
        "role": membership.role,
        "subscription_active": True,
    }