"""
Who can see and change what about a student. Every student route checks here, so
the School Hub and the app can only hide what the server already refuses.

- Owner, Admin, Head Teacher: everything.
- Teacher: reads marks and behaviour; changes marks, attendance and behaviour.
  Medical only for the class they are class teacher of (app.crud.class_teacher).
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

from app.crud import class_teacher, team
from app.db.models.student import Student
from app.db.models.users import User

FULL_ROLES = frozenset({"admin", "head_teacher"})


@dataclass(frozen=True)
class StudentAccess:
    # Medical, behaviour and marks. The list and profile are open to every member.
    view_records: bool
    # Every student's medical record. Class teachers also read their own class's.
    view_medical: bool
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
        view_medical=full or nurse,
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


def may_read_medical(access: StudentAccess, *, own_class: str | None, student: Student) -> bool:
    """Everyone with view_medical, and a class teacher for the students in their class."""
    if access.view_medical:
        return True
    return access.view_records and class_teacher.is_class_teacher_of(
        own_class, name=student.class_applied or "", stream=student.faculty
    )


async def require_medical_reader(
    db: AsyncSession, user: User, student_id: uuid.UUID
) -> StudentCaller:
    """The caller, refused with 403 unless they may read this student's medical record."""
    caller = await require(db, user, "view_records")
    student = await db.scalar(
        select(Student).where(Student.id == student_id, Student.tenant_id == caller.tenant_id)
    )
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    own = None
    if not caller.access.view_medical:
        own = await class_teacher.own_class(db, tenant_id=caller.tenant_id, user=user)
    if not may_read_medical(caller.access, own_class=own, student=student):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Medical records are for the nurse, the head teacher, admins and the "
            "student's class teacher.",
        )
    return caller


async def ensure_in_school(db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID) -> None:
    """404 for a student of another school, the same as one that doesn't exist."""
    found = await db.scalar(
        select(Student.id).where(Student.id == student_id, Student.tenant_id == tenant_id)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
