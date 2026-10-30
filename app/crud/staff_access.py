"""
Who can change staff records: the staff list, each staff member's details and
their qualifications. Owner, Admin and Head Teacher only; every member of the
school can read them (timetables, registers and the dashboard name staff).
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.crud.student_access import FULL_ROLES
from app.db.models.users import User


def can_edit_staff(*, is_owner: bool, role_name: str) -> bool:
    return is_owner or role_name in FULL_ROLES


async def require_staff_editor(db: AsyncSession, user: User) -> uuid.UUID:
    """The caller's tenant_id, refused with 403 unless their role can change staff records."""
    actor = await team.get_actor(db, user)
    if not can_edit_staff(is_owner=actor.is_owner, role_name=actor.role_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner, an admin or a head teacher can change staff records.",
        )
    return actor.tenant_id
