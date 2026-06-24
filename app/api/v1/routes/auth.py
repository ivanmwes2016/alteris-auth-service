from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.db import get_db
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.core.supabase import supabase, get_supabase

from fastapi import APIRouter, Depends, Header, HTTPException
from app.db.models.users import User
from app.helpers.jwt import get_user_from_token

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
async def me(authorization: str = Header(None), db: AsyncSession=Depends(get_db), supabase:Client = Depends(get_supabase)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")

    token = authorization.split(" ")[1]


    user = get_user_from_token(supabase, token)

    user_id = user.id
    email = user.email
    name = user.user_metadata.get("name") if user else None
    

    user_context = await get_user_context(db, user_id, supabase)

    return {
        "user": {
            "id": user_id,
            "email": email,
            "name": name
        },
        "tenant": user_context.get("tenant"),
        "role": user_context.get("role"),
        "subscription_active": user_context.get("subscription_active"),
    }


@router.post("/session")
async def create_session(
    response: Response,
    authorization: str = Header(None),
    supabase: Client = Depends(get_supabase),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]

    user = get_user_from_token(supabase, token)

    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )

    return {"ok": True, "user_id": user.id}



async def get_current_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    supabase_user = get_user_from_token(supabase, token)

    user = await db.get(User, supabase_user.id)

    if not user:
        user = User(
            id=supabase_user.id,
            email=supabase_user.email,
        )
        db.add(user)
        await db.flush()

    return user