from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.tenant_profile import TenantProfile
from app.db.models.tution_fees import TutionFees
from app.db.models.users import User

router = APIRouter()


class TutionFeesPatch(BaseModel):
    currency: str | None = None
    min_fee: int | None = None
    max_fee: int | None = None
    billing_period: str | None = None
    notes: str | None = None


class TutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str | None = None
    min_fee: int | None = None
    max_fee: int | None = None
    billing_period: str | None = None
    notes: str | None = None


class Location(BaseModel):
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


class Contact(BaseModel):
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class FeeDetails(BaseModel):
    description: str | None
    amount: str | None
    period: str | None


class Fees(BaseModel):
    currency: str | None = None
    details: list[FeeDetails]


class ProfilePatch(BaseModel):
    tenant_id: UUID | None = None
    cover_image: str | None = None
    about: str | None = None
    tagline: str | None = None

    courses: list[str] | None = None
    achievements: list[str] | None = None
    facilities: list[str] | None = None
    levels: list[str] | None = None
    curriculum: list[str] | None = None
    subjects: list[str] | None = None

    type: str | None = None
    gender: str | None = None
    founded: str | None = None
    mode: str | None = None

    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None

    fees: Fees | None = None
    location: Location | None = None
    contact: Contact | None = None


class TenantProfileResponse(BaseModel):
    name: str
    tagline: str | None = None
    about: str | None = None

    logo: str | None = None
    cover_image: str | None = None

    location: Location = Field(default_factory=Location)
    contact: Contact = Field(default_factory=Contact)

    levels: list[str] = Field(default_factory=list)
    curriculum: list[str] = Field(default_factory=list)

    type: str | None = None
    gender: str | None = None
    founded: str | None = None
    mode: str | None = None

    achievements: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    facilities: list[str] = Field(default_factory=list)

    fees: Fees = Field(default_factory=Fees)

    profile_completion: int = 0
    profile_views: int = 0
    enquiries: int = 0

    verified: bool = True
    premium: bool = False


@router.get("/", response_model=TenantProfileResponse)
async def get_school_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantProfileResponse:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )

    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to a school",
        )

    tenant_result = await db.execute(
        select(Tenant).where(Tenant.id == member.tenant_id)
    )

    tenant = tenant_result.scalar_one()

    profile_result = await db.execute(
        select(TenantProfile).where(TenantProfile.tenant_id == member.tenant_id)
    )

    profile = profile_result.scalar_one_or_none()

    fees_result = await db.execute(
        select(TutionFees).where(TutionFees.tenant_id == member.tenant_id)
    )

    fees_result.scalar_one_or_none()

    if profile is None:
        profile = TenantProfile(
            tenant_id=member.tenant_id,
            courses=[],
            achievements=[],
            facilities=[],
        )

        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    return TenantProfileResponse(
        name=tenant.name,
        logo=tenant.logo_path,
        cover_image=profile.cover_image,
        about=profile.about,
        mode=profile.mode,
        gender=profile.gender,
        founded=profile.founded,
        tagline=profile.tagline,
        curriculum=profile.curriculum or [],
        type=profile.type,
        location=Location(
            address=profile.location.get("address") if profile.location else None,
            city=profile.location.get("city") if profile.location else None,
            state=profile.location.get("state") if profile.location else None,
            country=profile.location.get("country") if profile.location else None,
        ),
        contact=Contact(
            phone=profile.contact.get("phone") if profile.contact else None,
            email=profile.contact.get("email") if profile.contact else None,
            website=profile.contact.get("website") if profile.contact else None,
        ),
        levels=profile.levels or [],
        courses=profile.courses or [],
        achievements=profile.achievements or [],
        facilities=profile.facilities or [],
        fees=Fees(
            currency=profile.fees.get("currency") if profile.fees else None,
            details=[
                FeeDetails(
                    description=item.get("description") or None,
                    amount=item.get("amount"),
                    period=item.get("period"),
                )
                for item in profile.fees.get("details", [])
            ]
            if profile.fees
            else [],
        ),
        subjects=profile.subjects or [],
        profile_completion=0,
        profile_views=0,
        enquiries=0,
        verified=True,
        premium=False,
    )


@router.patch("/")
async def patch_school_profile(
    payload: ProfilePatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProfilePatch:
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


@router.patch("/profile/fees")
async def patch_school_fees(
    payload: TutionFeesPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TutionFeesPatch:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )

    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=403, detail="User does not belong to a school")

    result = await db.execute(
        select(TutionFees).where(TutionFees.tenant_id == member.tenant_id)
    )

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
