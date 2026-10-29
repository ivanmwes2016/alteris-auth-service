"""
Who can see and change what about a student. Every student route checks here, so
the School Hub and the app can only hide what the server already refuses.

- Owner, Admin, Head Teacher: everything.
- Teacher: reads everything; changes marks, attendance and behaviour.
- Nurse: reads everything; changes medical.
- Admissions Officer, Bursar: the student list and profile only (for admissions,
  fees and the dashboard). Admissions Officers also add students when an applicant
  is accepted. Neither sees medical, behaviour or marks.

Registers have their own rules (app.crud.attendance), which give the same roles
attendance.
"""

import uuid
from dataclasses import asdict, dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.db.models.student import Student
from app.db.models.users import User

FULL_ROLES = frozenset({"admin", "head_teacher"})


@dataclass(frozen=True)
class StudentAccess:
    # Medical, behaviour and marks. The list and profile are open to every member.
    view_records: bool
    add_students: bool
    edit_details: bool  # the profile and its parents
    edit_marks: bool
    edit_attendance: bool  # notes; registers have their own rules in app.crud.attendance
    edit_behaviour: bool
    edit_medical: bool

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


def student_access(*, is_owner: bool, role_name: str) -> StudentAccess:
    full = is_owner or role_name in FULL_ROLES
    teacher = role_name == "teacher"
    nurse = role_name == "nurse"
    return StudentAccess(
        view_records=full or teacher or nurse,
        add_students=full or role_name == "admissions_officer",
        edit_details=full,
        edit_marks=full or teacher,
        edit_attendance=full or teacher,
        edit_behaviour=full or teacher,
        edit_medical=full or nurse,
    )


@dataclass(frozen=True)
class StudentCaller:
    tenant_id: uuid.UUID
    access: StudentAccess


async def get_caller(db: AsyncSession, user: User) -> StudentCaller:
    actor = await team.get_actor(db, user)
    return StudentCaller(
        tenant_id=actor.tenant_id,
        access=student_access(is_owner=actor.is_owner, role_name=actor.role_name),
    )


async def require(db: AsyncSession, user: User, permission: str) -> StudentCaller:
    """The caller, refused with 403 unless their role has `permission` (a StudentAccess field)."""
    caller = await get_caller(db, user)
    if not getattr(caller.access, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role can't do that for students. Ask your school admin.",
        )
    return caller


async def ensure_in_school(db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID) -> None:
    """404 for a student of another school, the same as one that doesn't exist."""
    found = await db.scalar(
        select(Student.id).where(Student.id == student_id, Student.tenant_id == tenant_id)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
