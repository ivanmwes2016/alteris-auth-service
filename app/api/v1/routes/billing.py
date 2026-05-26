import stripe

from fastapi import APIRouter, Request

from app.core.config import get_settings
config = get_settings()

router = APIRouter()

stripe.api_key = config.STRIPE_SECRET_KEY

prices ={
    1: 0,
    2: 20,
    3: 250
}


@router.post("/checkout")
async def create_checkout(
    payload: dict,
    request: Request,
):
    tenant_id = request.state.tenant_id

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price": payload["price_id"],
                "quantity": 1,
            }
        ],
        success_url="https://app.com/success",
        cancel_url="https://app.com/cancel",
        metadata={
            "tenant_id": tenant_id,
        },
    )

    return {
        "checkout_url": session.url,
    }


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()

    signature = request.headers.get("stripe-signature")

    event = stripe.Webhook.construct_event(
        payload,
        signature,
        config.STRIPE_WEBHOOK_SECRET,
    )

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        tenant_id = session["metadata"]["tenant_id"]

        # Update tenant plan in database

    return {"received": True}