"""
The old per-student attendance endpoints. Attendance is now taken in
registers (/attendance): `records` here are the student's daily-register
marks, read-only. Only the profile's free-text `notes` can still be changed.
tenant_id always comes from the authenticated user, and the student must
belong to that school.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud import student_access
from app.crud.attendance import daily_records
from app.crud.student_attendance_profile import get_or_create_profile, sync_profile
from app.db.models.student import Student
from app.db.models.users import User
from app.db.schemas.attendance_profile import (
    AttendanceProfileBulkUpdate,
    AttendanceProfileRead,
    RecordRead,
)

router = APIRouter()


async def _school_student(db: AsyncSession, student_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    found = await db.scalar(
        select(Student.id).where(Student.id == student_id, Student.tenant_id == tenant_id)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")


async def _read(
    db: AsyncSession, student_id: uuid.UUID, tenant_id: uuid.UUID
) -> AttendanceProfileRead:
    profile = await get_or_create_profile(db, student_id=student_id, tenant_id=tenant_id)
    await db.commit()
    records = (await daily_records(db, tenant_id, [student_id])).get(student_id, [])
    return AttendanceProfileRead(
        id=profile.id,
        student_id=student_id,
        notes=profile.notes,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        records=[RecordRead.model_validate(r) for r in records],
    )


@router.get("", response_model=AttendanceProfileRead)
async def get_attendance_profile(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceProfileRead:
    tenant_id = await get_current_tenant_id(db, current_user)
    await _school_student(db, student_id, tenant_id)
    return await _read(db, student_id, tenant_id)


@router.patch("", response_model=AttendanceProfileRead)
async def update_attendance_notes(
    student_id: uuid.UUID,
    data: AttendanceProfileBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttendanceProfileRead:
    """Updates `notes` only. Sending `records` is refused: take attendance in registers."""
    if data.records is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Attendance is now taken in registers (/attendance); records can't be set here",
        )
    tenant_id = (await student_access.require(db, current_user, "edit_attendance")).tenant_id
    await _school_student(db, student_id, tenant_id)
    await sync_profile(db, student_id=student_id, tenant_id=tenant_id, data=data)
    return await _read(db, student_id, tenant_id)
