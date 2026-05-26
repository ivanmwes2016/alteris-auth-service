from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember

from fastapi import APIRouter, Depends, Header, HTTPException
from app.helpers.jwt import verify_jwt

from app.helpers.user_context import get_user_context
router = APIRouter()



@router.post("/signup")
async def signup(payload: dict, db: AsyncSession = Depends(get_db)):
    """
    Expected payload:
    {
      email,
      password,
      company_name
    }
    """

    # 1. Create Supabase user
    # NOTE:
    # In production use Supabase Admin SDK

    user_id = "generated-user-id"

    # 2. Create tenant

    tenant = Tenant(
        name=payload["company_name"],
        plan="free",
        seat_limit=2,
    )

    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    # 3. Create owner membership

    membership = TenantMember(
        tenant_id=tenant.id,
        user_id=user_id,
        role="owner",
    )

    db.add(membership)
    await db.commit()

    return {
        "message": "signup successful",
        "tenant_id": str(tenant.id),
    }

@router.get("/me")
def me(authorization: str = Header(None), db=Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")

    token = authorization.split(" ")[1]

    # 1. verify JWT from Supabase
    payload = verify_jwt(token)

    user_id = payload.get("sub")
    email = payload.get("email")

    # 2. build full SaaS context
    user_context = get_user_context(db, user_id)

    return {
        "user": {
            "id": user_id,
            "email": email,
        },
        "tenant": user_context.get("tenant"),
        "role": user_context.get("role"),
        "subscription_active": user_context.get("subscription_active"),
    }