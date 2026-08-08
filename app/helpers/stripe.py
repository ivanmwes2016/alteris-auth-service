from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stripe.checkout import Session

from app.db.models.tenant import Tenant


async def handle_checkout_completed(session: Session, db: AsyncSession) -> Tenant | None:
    tenant_id = session.get("metadata", {}).get("tenant_id")

    if not tenant_id:
        return None

    tenant = await db.get(Tenant, tenant_id)

    if not tenant:
        return None

    tenant.stripe_customer_id = session.get("customer")
    tenant.stripe_subscription_id = session.get("subscription")
    tenant.plan = session.get("metadata", {}).get("plan", "pro")
    tenant.seat_limit = int(session.get("metadata", {}).get("seat_limit", 10))

    await db.commit()
    return tenant


async def handle_subscription_updated(subscription: Mapping[str, object], db: AsyncSession) -> None:
    stripe_subscription_id = subscription.get("id")
    status = subscription.get("status")

    result = await db.execute(
        select(Tenant).where(Tenant.stripe_subscription_id == stripe_subscription_id)
    )

    tenant = result.scalar_one_or_none()

    if not tenant:
        return

    if status in ["active", "trialing"]:
        tenant.plan = tenant.plan or "pro"
    else:
        tenant.plan = "free"


async def handle_subscription_deleted(subscription: Mapping[str, object], db: AsyncSession) -> None:
    stripe_subscription_id = subscription.get("id")

    result = await db.execute(
        select(Tenant).where(Tenant.stripe_subscription_id == stripe_subscription_id)
    )

    tenant = result.scalar_one_or_none()

    if not tenant:
        return

    tenant.plan = "free"
    tenant.seat_limit = 5
    tenant.stripe_subscription_id = None

    await db.commit()
