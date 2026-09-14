"""
Two endpoints for the whole Attendance tab. tenant_id always comes from the
authenticated user, never from the request body/query.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.student_attendance_profile import get_or_create_profile, sync_profile
from app.db.models.attendance import StudentAttendanceProfile
from app.db.models.users import User
from app.db.schemas.attendance_profile import AttendanceProfileBulkUpdate, AttendanceProfileRead

router = APIRouter()


@router.get("", response_model=AttendanceProfileRead)
async def get_attendance_profile(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentAttendanceProfile:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await get_or_create_profile(db, student_id=student_id, tenant_id=tenant_id)


@router.patch("", response_model=AttendanceProfileRead)
async def sync_attendance_profile(
    student_id: uuid.UUID,
    data: AttendanceProfileBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentAttendanceProfile:
    """
    Send the profile's `notes` plus `records` if it changed.
    Each record's `id`: present+matching -> update, missing -> create.
    Any existing record whose id is left out of the list gets deleted.
    Omit `records` entirely to leave it untouched.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    return await sync_profile(db, student_id=student_id, tenant_id=tenant_id, data=data)
