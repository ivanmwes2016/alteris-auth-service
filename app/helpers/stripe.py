from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.tenant import Tenant
async def handle_checkout_completed(session, db: AsyncSession):
    tenant_id = session.get("metadata", {}).get("tenant_id")

    if not tenant_id:
        return

    tenant = await db.get(Tenant, tenant_id)

    if not tenant:
        return

    tenant.stripe_customer_id = session.get("customer")
    tenant.stripe_subscription_id = session.get("subscription")
    tenant.plan = session.get("metadata", {}).get("plan", "pro")
    tenant.seat_limit = int(session.get("metadata", {}).get("seat_limit", 10))


async def handle_subscription_updated(subscription, db: AsyncSession):
    stripe_subscription_id = subscription.get("id")
    status = subscription.get("status")

    result = await db.execute(
        select(Tenant).where(
            Tenant.stripe_subscription_id == stripe_subscription_id
        )
    )

    tenant = result.scalar_one_or_none()

    if not tenant:
        return

    if status in ["active", "trialing"]:
        tenant.plan = tenant.plan or "pro"
    else:
        tenant.plan = "free"


async def handle_subscription_deleted(subscription, db: AsyncSession):
    stripe_subscription_id = subscription.get("id")

    result = await db.execute(
        select(Tenant).where(
            Tenant.stripe_subscription_id == stripe_subscription_id
        )
    )

    tenant = result.scalar_one_or_none()

    if not tenant:
        return

    tenant.plan = "free"
    tenant.seat_limit = 5
    tenant.stripe_subscription_id = None