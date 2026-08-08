import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.config import get_settings
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.helpers.jwt import get_user_from_token
from app.helpers.user_context import get_user_context

config = get_settings()

router = APIRouter()

stripe.api_key = config.STRIPE_SECRET_KEY

prices = {1: 0, 2: 20, 3: 250}


class CheckoutPayload(BaseModel):
    price_id: str
    price: int
    tenant_id: str
    plan: str


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutPayload,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )

    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(supabase, token)

    user_context = await get_user_context(db, user.id, supabase)
    tenant_id = user_context["tenant"]["id"]

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": payload.price_id, "quantity": 1}],
        success_url="http://localhost:3000/settings/billing?success=true",
        cancel_url="http://localhost:3000/settings/billing?cancelled=true",
        metadata={
            "tenant_id": str(tenant_id),
            "plan": payload.plan,
        },
        subscription_data={
            "metadata": {
                "tenant_id": str(tenant_id),
                "plan": payload.plan,
            }
        },
    )

    if session.url is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe did not return a checkout URL",
        )

    return {"checkout_url": session.url}
