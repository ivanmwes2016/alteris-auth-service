# app/api/routes/stripe_webhooks.py

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.helpers.stripe import (
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
)

settings = get_settings()


router = APIRouter()

stripe.api_key = settings.STRIPE_SECRET_KEY


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    payload = await request.body()

    if stripe_signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature",
        )

    try:
        event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
            payload=payload,
            sig_header=stripe_signature,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}") from e
    except stripe.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await handle_checkout_completed(data, db)

    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(data, db)

    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(data, db)

    await db.commit()

    return {"received": True}
