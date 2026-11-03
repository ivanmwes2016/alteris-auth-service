"""
Class teachers: staff ranked "Class Teacher", with the class they look after named
as free text on their staff record (class_assigned). Timetables let them change
their own class's lessons; students' medical records open to them for that class.

Only models are imported here, so student_access, attendance and timetable can all
use it without importing each other.
"""

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.staff import Staff
from app.db.models.users import User

CLASS_TEACHER_RANK = "Class Teacher"


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def is_class_teacher_of(class_assigned: str | None, *, name: str, stream: str | None) -> bool:
    """Staff records name their class as free text ("Grade 9A", "grade 9 a"), so compare
    ignoring case, spaces and punctuation against the class's name plus its stream."""
    if not class_assigned or not _squash(class_assigned):
        return False
    return _squash(class_assigned) == _squash(name + (stream or ""))


async def linked_staff_id(
    db: AsyncSession, *, tenant_id: uuid.UUID, user: User
) -> uuid.UUID | None:
    """The staff record an admin linked to this account, else the one with its email."""
    linked = await db.scalar(
        select(Staff.id).where(Staff.tenant_id == tenant_id, Staff.user_id == user.id)
    )
    if linked is not None:
        return linked
    matched: uuid.UUID | None = await db.scalar(
        select(Staff.id)
        .where(
            Staff.tenant_id == tenant_id,
            Staff.user_id.is_(None),
            func.lower(Staff.email) == user.email.lower(),
        )
        .order_by(Staff.created_at)
        .limit(1)
    )
    return matched


async def own_class(db: AsyncSession, *, tenant_id: uuid.UUID, user: User) -> str | None:
    """The class this account is class teacher of, as their staff record writes it."""
    staff_id = await linked_staff_id(db, tenant_id=tenant_id, user=user)
    staff = await db.get(Staff, staff_id) if staff_id else None
    if staff is None or staff.rank != CLASS_TEACHER_RANK:
        return None
    return (staff.class_assigned or "").strip() or None
