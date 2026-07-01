from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.db.models.tenant_member import TenantMember
from app.db.models.tution_fees import TutionFees
from app.db.models.users import User

router = APIRouter()


class SchoolFeesPatch(BaseModel):
    currency: str | None = None
    min_fee: int | None = None
    max_fee: int | None = None
    billing_period: str | None = None
    notes: str | None = None


class TenantProfile(BaseModel):
    id: str
    tenant_id: str

    about: str | None = None
    tagline: str | None = None

    courses: list[str] | None = None
    achievements: list[str] | None = None
    facilities: list[str] | None = None

    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None


@router.patch("/school-profile")
async def patch_school_profile(
    payload: TenantProfile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantProfile:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )

    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=403, detail="User does not belong to a school")

    profile_result = await db.execute(
        select(TenantProfile).where(TenantProfile.tenant_id == member.tenant_id)
    )

    profile = profile_result.scalar_one_or_none()

    if not profile:
        profile = TenantProfile(tenant_id=member.tenant_id)
        db.add(profile)

    updates = payload.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(profile, field, value)

    try:
        await db.commit()
        await db.refresh(profile)
        return profile

    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while updating school profile.",
        ) from exc


@router.patch("/profile/tution-fees")
async def patch_school_fees(
    payload: SchoolFeesPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TutionFees:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )

    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=403, detail="User does not belong to a school")

    result = await db.execute(select(TutionFees).where(TutionFees.tenant_id == member.tenant_id))

    fees = result.scalar_one_or_none()

    if not fees:
        fees = TutionFees(tenant_id=member.tenant_id)
        db.add(fees)

    updates = payload.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(fees, field, value)

    await db.commit()
    await db.refresh(fees)

    return fees
