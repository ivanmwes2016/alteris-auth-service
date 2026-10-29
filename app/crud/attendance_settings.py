"""
The settings attendance depends on: the school's timezone, term dates and
no-school days (approvers), each student's subjects offered (anyone who takes
registers, with history), which app account a staff member signs in with
(approvers), and the phones that get attendance pushes.
"""

import uuid
from itertools import pairwise

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.crud.attendance import Viewer, require_approver, timezone_source, user_names
from app.db.models.academic_term import AcademicTermDates
from app.db.models.academics import Subject
from app.db.models.attendance import NoSchoolDay
from app.db.models.push_token import PushToken
from app.db.models.staff import Staff
from app.db.models.student import Student, StudentSubject, StudentSubjectChange
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.schemas.attendance import (
    AttendanceSettingsRead,
    AttendanceSettingsUpdate,
    NoSchoolDayCreate,
    NoSchoolDayRead,
    PersonRead,
    StaffAccountRead,
    StaffAccountWrite,
    StudentSubjectsRead,
    StudentSubjectsWrite,
    SubjectChangeRead,
    SubjectRef,
    TermDates,
    TermDatesWrite,
)
from app.helpers.timezones import is_valid_timezone, school_timezone

SUBJECT_HISTORY_LIMIT = 50


# ---------------------------------------------------------------------------
# Timezone (Owner and Admins)
# ---------------------------------------------------------------------------
def can_change_timezone(viewer: Viewer) -> bool:
    return viewer.actor.is_owner or viewer.actor.role_name == "admin"


def settings_read(viewer: Viewer) -> AttendanceSettingsRead:
    return AttendanceSettingsRead(
        timezone=viewer.tz.key,
        source=viewer.tz_source,
        country=viewer.country,
        approver=viewer.approver,
        can_change_timezone=can_change_timezone(viewer),
    )


async def update_settings(
    db: AsyncSession, viewer: Viewer, data: AttendanceSettingsUpdate
) -> AttendanceSettingsRead:
    if not can_change_timezone(viewer):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the owner or an admin can change the timezone"
        )
    if data.timezone is not None and not is_valid_timezone(data.timezone):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That isn't a known timezone")
    tenant = await db.get(Tenant, viewer.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found")
    tenant.timezone = data.timezone
    await db.commit()
    tz = school_timezone(explicit=tenant.timezone, country=tenant.country)
    return AttendanceSettingsRead(
        timezone=tz.key,
        source=timezone_source(explicit=tenant.timezone, country=tenant.country),
        country=tenant.country,
        approver=viewer.approver,
        can_change_timezone=True,
    )


# ---------------------------------------------------------------------------
# Term dates and no-school days (approvers)
# ---------------------------------------------------------------------------
async def list_term_dates(db: AsyncSession, tenant_id: uuid.UUID) -> list[TermDates]:
    rows = await db.scalars(
        select(AcademicTermDates)
        .where(AcademicTermDates.tenant_id == tenant_id)
        .order_by(AcademicTermDates.starts_on)
    )
    return [TermDates.model_validate(r) for r in rows]


async def replace_term_dates(
    db: AsyncSession, viewer: Viewer, data: TermDatesWrite
) -> list[TermDates]:
    require_approver(viewer)
    terms = sorted(data.terms, key=lambda t: t.starts_on)
    if len({(t.label, t.year) for t in terms}) != len(terms):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Each term can only appear once")
    for earlier, later in pairwise(terms):
        if later.starts_on <= earlier.ends_on:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{earlier.label} {earlier.year} and {later.label} {later.year} overlap",
            )
    await db.execute(
        delete(AcademicTermDates).where(AcademicTermDates.tenant_id == viewer.tenant_id)
    )
    db.add_all(
        AcademicTermDates(
            tenant_id=viewer.tenant_id,
            label=t.label,
            year=t.year,
            starts_on=t.starts_on,
            ends_on=t.ends_on,
        )
        for t in terms
    )
    await db.commit()
    return await list_term_dates(db, viewer.tenant_id)


async def list_no_school_days(
    db: AsyncSession, viewer: Viewer, *, year: int | None
) -> list[NoSchoolDayRead]:
    query = select(NoSchoolDay).where(NoSchoolDay.tenant_id == viewer.tenant_id)
    if year is not None:
        query = query.where(func.extract("year", NoSchoolDay.date) == year)
    return [
        NoSchoolDayRead.model_validate(d)
        for d in await db.scalars(query.order_by(NoSchoolDay.date))
    ]


async def add_no_school_day(
    db: AsyncSession, viewer: Viewer, data: NoSchoolDayCreate
) -> NoSchoolDayRead:
    require_approver(viewer)
    day = NoSchoolDay(
        tenant_id=viewer.tenant_id, date=data.date, reason=data.reason, created_by=viewer.user_id
    )
    db.add(day)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That day is already a no-school day"
        ) from exc
    return NoSchoolDayRead.model_validate(day)


async def remove_no_school_day(db: AsyncSession, viewer: Viewer, day_id: uuid.UUID) -> None:
    require_approver(viewer)
    result = await db.execute(
        delete(NoSchoolDay).where(
            NoSchoolDay.id == day_id, NoSchoolDay.tenant_id == viewer.tenant_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No-school day not found")
    await db.commit()


# ---------------------------------------------------------------------------
# Subjects offered (anyone who takes registers)
# ---------------------------------------------------------------------------
async def _student(db: AsyncSession, viewer: Viewer, student_id: uuid.UUID) -> Student:
    student = await db.scalar(
        select(Student).where(Student.id == student_id, Student.tenant_id == viewer.tenant_id)
    )
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return student


async def get_student_subjects(
    db: AsyncSession, viewer: Viewer, student_id: uuid.UUID
) -> StudentSubjectsRead:
    student = await _student(db, viewer, student_id)
    subjects = await db.scalars(
        select(Subject)
        .join(StudentSubject, StudentSubject.subject_id == Subject.id)
        .where(StudentSubject.student_id == student.id)
        .order_by(Subject.name)
    )
    changes = list(
        await db.scalars(
            select(StudentSubjectChange)
            .where(StudentSubjectChange.student_id == student.id)
            .order_by(StudentSubjectChange.changed_at.desc())
            .limit(SUBJECT_HISTORY_LIMIT)
        )
    )
    names = await user_names(db, (c.changed_by for c in changes))
    return StudentSubjectsRead(
        subjects=[SubjectRef.model_validate(s) for s in subjects],
        history=[
            SubjectChangeRead(
                subject_name=c.subject_name,
                action=c.action,
                changed_by=PersonRead(
                    id=c.changed_by, name=names.get(c.changed_by) if c.changed_by else None
                ),
                changed_at=c.changed_at,
            )
            for c in changes
        ],
    )


async def set_student_subjects(
    db: AsyncSession, viewer: Viewer, student_id: uuid.UUID, data: StudentSubjectsWrite
) -> StudentSubjectsRead:
    """Replaces the list. An empty list means the student takes all their class's subjects."""
    student = await _student(db, viewer, student_id)
    wanted = set(data.subject_ids)
    subjects = {
        s.id: s
        for s in await db.scalars(
            select(Subject).where(Subject.tenant_id == viewer.tenant_id, Subject.id.in_(wanted))
        )
    }
    if len(subjects) != len(wanted):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Some of these subjects don't exist"
        )

    current = {
        row.subject_id: row
        for row in await db.scalars(
            select(StudentSubject).where(StudentSubject.student_id == student.id)
        )
    }
    current_names = dict(
        (await db.execute(select(Subject.id, Subject.name).where(Subject.id.in_(list(current)))))
        .tuples()
        .all()
    )
    for subject_id in wanted - current.keys():
        db.add(
            StudentSubject(tenant_id=viewer.tenant_id, student_id=student.id, subject_id=subject_id)
        )
        db.add(_subject_change(viewer, student, subjects[subject_id].name, "added"))
    for subject_id in current.keys() - wanted:
        await db.delete(current[subject_id])
        db.add(
            _subject_change(
                viewer, student, current_names.get(subject_id, "Unknown subject"), "removed"
            )
        )
    await db.commit()
    return await get_student_subjects(db, viewer, student.id)


def _subject_change(
    viewer: Viewer, student: Student, name: str, action: str
) -> StudentSubjectChange:
    return StudentSubjectChange(
        tenant_id=viewer.tenant_id,
        student_id=student.id,
        subject_name=name,
        action=action,
        changed_by=viewer.user_id,
    )


# ---------------------------------------------------------------------------
# Staff accounts (approvers)
# ---------------------------------------------------------------------------
async def set_staff_account(
    db: AsyncSession, viewer: Viewer, staff_id: uuid.UUID, data: StaffAccountWrite
) -> StaffAccountRead:
    require_approver(viewer)
    staff = await db.scalar(
        select(Staff).where(Staff.id == staff_id, Staff.tenant_id == viewer.tenant_id)
    )
    if staff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    if data.user_id is not None:
        member = await db.scalar(
            select(TenantMember.id).where(
                TenantMember.tenant_id == viewer.tenant_id,
                TenantMember.user_id == data.user_id,
                TenantMember.status == "active",
            )
        )
        if member is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "That account isn't a member of this school"
            )
    staff.user_id = data.user_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That account is already linked to another staff member"
        ) from exc
    return StaffAccountRead(
        staff_id=staff.id, user_id=staff.user_id, linked_by="admin" if staff.user_id else None
    )


# ---------------------------------------------------------------------------
# Push tokens (any member)
# ---------------------------------------------------------------------------
async def register_push_token(db: AsyncSession, actor: team.Actor, token: str) -> None:
    """A phone that signs in to another account moves to it."""
    await db.execute(
        pg_insert(PushToken)
        .values(id=uuid.uuid4(), token=token, user_id=actor.user_id, tenant_id=actor.tenant_id)
        .on_conflict_do_update(
            index_elements=["token"],
            set_={"user_id": actor.user_id, "tenant_id": actor.tenant_id, "updated_at": func.now()},
        )
    )
    await db.commit()


async def unregister_push_token(db: AsyncSession, actor: team.Actor, token: str) -> None:
    await db.execute(
        delete(PushToken).where(PushToken.token == token, PushToken.user_id == actor.user_id)
    )
    await db.commit()
