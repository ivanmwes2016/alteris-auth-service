import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.db.models.tenant_member import TenantMember
from app.db.models.tenant import Tenant

router = APIRouter()


@router.post("/invite")
async def invite_member(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    tenant_id = request.state.tenant_id

    tenant_result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )

    tenant = tenant_result.scalar_one()

    count_result = await db.execute(
        select(func.count())
        .select_from(TenantMember)
        .where(TenantMember.tenant_id == tenant_id)
    )

    current_members = count_result.scalar()

    if current_members >= tenant.seat_limit:
        raise HTTPException(
            status_code=403,
            detail="Seat limit reached"
        )

    invite_token = secrets.token_urlsafe(32)

    # TODO:
    # Store invitation table
    # Send email

    return {
        "message": "invite created",
        "invite_token": invite_token,
    }