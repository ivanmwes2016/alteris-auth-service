from pydantic import BaseModel
import stripe

from fastapi import APIRouter, Depends, Header, Request
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

prices ={
    1: 0,
    2: 20,
    3: 250
}

class CheckoutPayload(BaseModel):
    price_id: str
    price: int
    tenant_id: str


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutPayload,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
):
    token = authorization.split(" ")[1]
    user = get_user_from_token(supabase, token)

    

    user_context = await get_user_context(db, user.id)
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

    return {"checkout_url": session.url}