"""
Who can change staff records: the staff list, each staff member's details and
their qualifications. Owner, Admin and Head Teacher only; every member of the
school can read them (timetables, registers and the dashboard name staff).

A staff member's documents and health record are private: only the same roles
read them too, except that the school nurse also reads and changes health.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.crud.student_access import FULL_ROLES
from app.db.models.staff import Staff
from app.db.models.users import User


def can_edit_staff(*, is_owner: bool, role_name: str) -> bool:
    return is_owner or role_name in FULL_ROLES


def can_manage_staff_health(*, is_owner: bool, role_name: str) -> bool:
    return can_edit_staff(is_owner=is_owner, role_name=role_name) or role_name == "nurse"


async def require_staff_editor(db: AsyncSession, user: User) -> uuid.UUID:
    """The caller's tenant_id, refused with 403 unless their role can change staff records."""
    actor = await team.get_actor(db, user)
    if not can_edit_staff(is_owner=actor.is_owner, role_name=actor.role_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner, an admin or a head teacher can change staff records.",
        )
    return actor.tenant_id


async def require_staff_health(db: AsyncSession, user: User) -> uuid.UUID:
    """The caller's tenant_id, refused with 403 unless their role looks after staff health."""
    actor = await team.get_actor(db, user)
    if not can_manage_staff_health(is_owner=actor.is_owner, role_name=actor.role_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner, an admin, a head teacher or the nurse can see staff health.",
        )
    return actor.tenant_id


async def ensure_staff_in_school(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """404 for a staff member of another school, the same as one that doesn't exist."""
    found = await db.scalar(
        select(Staff.id).where(Staff.id == staff_id, Staff.tenant_id == tenant_id)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
