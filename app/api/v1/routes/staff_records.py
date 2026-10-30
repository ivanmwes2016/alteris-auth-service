"""
The Documents and Health tabs on a staff profile. Private records: only the
owner, admins and head teachers read or change them (app.crud.staff_access).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import staff_records
from app.crud.staff_access import ensure_staff_in_school, require_staff_editor
from app.db.models.users import User
from app.db.schemas.staff_records import (
    StaffDocumentsRead,
    StaffDocumentsWrite,
    StaffHealthRead,
    StaffHealthWrite,
)

router = APIRouter()


async def _school_of(db: AsyncSession, user: User, staff_id: uuid.UUID) -> uuid.UUID:
    tenant_id = await require_staff_editor(db, user)
    await ensure_staff_in_school(db, staff_id=staff_id, tenant_id=tenant_id)
    return tenant_id


@router.get("/documents", response_model=StaffDocumentsRead)
async def get_staff_documents(
    staff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffDocumentsRead:
    tenant_id = await _school_of(db, current_user, staff_id)
    return await staff_records.get_documents(db, staff_id=staff_id, tenant_id=tenant_id)


@router.patch("/documents", response_model=StaffDocumentsRead)
async def save_staff_documents(
    staff_id: uuid.UUID,
    data: StaffDocumentsWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffDocumentsRead:
    """The whole list: matching ids are updated, others added, any left out deleted."""
    tenant_id = await _school_of(db, current_user, staff_id)
    return await staff_records.save_documents(db, staff_id=staff_id, tenant_id=tenant_id, data=data)


@router.get("/health", response_model=StaffHealthRead)
async def get_staff_health(
    staff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffHealthRead:
    tenant_id = await _school_of(db, current_user, staff_id)
    return await staff_records.get_health(db, staff_id=staff_id, tenant_id=tenant_id)


@router.patch("/health", response_model=StaffHealthRead)
async def save_staff_health(
    staff_id: uuid.UUID,
    data: StaffHealthWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffHealthRead:
    """Fields left out stay as they are; a list sent replaces the saved one."""
    tenant_id = await _school_of(db, current_user, staff_id)
    return await staff_records.save_health(db, staff_id=staff_id, tenant_id=tenant_id, data=data)
