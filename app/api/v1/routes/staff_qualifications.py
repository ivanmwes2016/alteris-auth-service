"""
Two endpoints for the Staff Qualifications tab. tenant_id always comes from
the authenticated user, never from the request body/query.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.staff_qualification_profile import get_or_create_profile, sync_profile
from app.db.models.staff import StaffQualificationProfile
from app.db.models.users import User
from app.db.schemas.staff_qualification_profile import (
    QualificationProfileBulkUpdate,
    QualificationProfileRead,
)

router = APIRouter()


@router.get("", response_model=QualificationProfileRead)
async def get_qualification_profile(
    staff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffQualificationProfile:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await get_or_create_profile(db, staff_id=staff_id, tenant_id=tenant_id)


@router.patch("", response_model=QualificationProfileRead)
async def sync_qualification_profile(
    staff_id: uuid.UUID,
    data: QualificationProfileBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffQualificationProfile:
    """
    Send the profile's `notes` plus `qualifications` if it changed.
    Each item's `id`: present+matching -> update, missing -> create.
    Any existing record whose id is left out of the list gets deleted.
    Omit `qualifications` entirely to leave it untouched.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    return await sync_profile(db, staff_id=staff_id, tenant_id=tenant_id, data=data)
