from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.db.models.enquiries import Enquiry
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User

router = APIRouter()


class EnquiryPayload(BaseModel):
    tenant_id: UUID
    name: str
    class_applied: str
    parent: str
    phone: str
    email: str
    stage: str
    officer: str
    previous_school: str
    notes: str


class EnquiryGetResponse(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str
    classApplied: str
    stage: str
    date: datetime
    created_at: datetime
    parent: str
    previousSchool: str | None
    photo: str | None = None
    notes: str | None = ""
    officer: str | None = ""


@router.post("/enquiries", status_code=201)
async def create_enquiry(
    payload: EnquiryPayload,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    try:
        enquiry = Enquiry(
            tenant_id=payload.tenant_id,
            name=payload.name,
            class_applied=payload.class_applied,
            parent=payload.parent,
            phone=payload.phone,
            email=payload.email,
            stage=payload.stage,
            officer=payload.officer,
            previous_school=payload.previous_school,
            notes=payload.notes,
        )

        db.add(enquiry)
        await db.commit()
        await db.refresh(enquiry)

        return {
            "message": "Enquiry created successfully",
        }

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not create enquiry because related data is invalid or already exists.",
        ) from exc

    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while creating enquiry.",
        ) from exc


@router.get("/enquiries", response_model=list[EnquiryGetResponse])
async def list_enquiries(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EnquiryGetResponse]:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )

    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=403, detail="User does not belong to a school")

    result = await db.execute(select(Enquiry).where(Enquiry.tenant_id == member.tenant_id))

    enquiries = result.scalars().all()

    result = [
        {
            **item.__dict__,
            "classApplied": item.class_applied,
            "previousSchool": item.previous_school,
            "date": item.created_at,
        }
        for item in enquiries
    ]

    return result
