"""
The sick bay: a teacher sends a student with what's wrong ("waiting"), the nurse
marks them arrived ("in") and closes the visit with where they went next: back
to class, home, or to hospital. Health alerts list the students whose allergies,
conditions or medicines staff should know about.

Every query is scoped by the caller's school. Permissions aren't checked yet:
any member of the school can use the sick bay.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.crud import student_access, team
from app.crud.attendance import LISTED_STUDENT_STATUSES, now_utc, school_today
from app.db.models.medical_note import StudentAllergy, StudentMedicalProfile
from app.db.models.sick_bay import SickBayVisit
from app.db.models.student import Student
from app.db.models.tenant import Tenant
from app.db.models.users import User
from app.db.schemas.sick_bay import (
    AlertAllergy,
    AlertCondition,
    AlertMedication,
    HealthAlert,
    HealthAlerts,
    SickBayDay,
    VisitCreate,
    VisitRead,
    VisitStudent,
    VisitUpdate,
)
from app.helpers.timezones import school_timezone

OPEN_STATUSES = frozenset({"waiting", "in"})
CLOSED_STATUSES = frozenset({"back", "home", "hospital"})


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def apply_update(
    visit: SickBayVisit, changes: dict[str, Any], *, now: datetime, by: str | None
) -> None:
    """
    Apply the fields sent. Arriving stamps arrived_at; closing stamps closed_at and
    who closed it. A closed visit can change outcome (a correction) but not reopen.
    """
    new_status = changes.get("status")
    if new_status is not None and new_status != visit.status:
        if visit.status in CLOSED_STATUSES and new_status in OPEN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This visit is closed. Send the student again for a new visit.",
            )
        if new_status == "in" and visit.arrived_at is None:
            visit.arrived_at = now
        if new_status in CLOSED_STATUSES:
            visit.arrived_at = visit.arrived_at or now
            visit.closed_at = now
            visit.closed_by_name = by
        visit.status = new_status
    for field in ("temperature", "treatment", "nurse_notes"):
        if field in changes:
            setattr(visit, field, changes[field])


def has_alert(profile: StudentMedicalProfile) -> bool:
    """Something staff should know: an allergy, a condition, a medicine still taken, or notes."""
    return bool(
        profile.allergies
        or profile.conditions
        or any(m.is_active for m in profile.medications)
        or (profile.emergency_notes or "").strip()
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Caller:
    tenant_id: uuid.UUID
    name: str | None
    today: date
    now: datetime


async def get_caller(db: AsyncSession, user: User) -> Caller:
    actor = await team.get_actor(db, user)
    tenant = await db.get(Tenant, actor.tenant_id)
    tz = school_timezone(
        explicit=tenant.timezone if tenant else None, country=tenant.country if tenant else None
    )
    now = now_utc()
    return Caller(tenant_id=actor.tenant_id, name=user.name, today=school_today(tz, now), now=now)


def _listed() -> ColumnElement[bool]:
    return func.lower(func.trim(Student.status)).in_(LISTED_STUDENT_STATUSES)


async def _allergy_names(
    db: AsyncSession, tenant_id: uuid.UUID, student_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not student_ids:
        return {}
    rows = await db.execute(
        select(StudentMedicalProfile.student_id, StudentAllergy.name)
        .join(StudentAllergy, StudentAllergy.profile_id == StudentMedicalProfile.id)
        .where(
            StudentMedicalProfile.tenant_id == tenant_id,
            StudentMedicalProfile.student_id.in_(student_ids),
        )
        .order_by(StudentAllergy.name)
    )
    names: dict[uuid.UUID, list[str]] = defaultdict(list)
    for student_id, name in rows:
        names[student_id].append(name)
    return names


def _read(visit: SickBayVisit, student: Student, allergies: list[str]) -> VisitRead:
    return VisitRead(
        id=visit.id,
        student=VisitStudent(
            id=student.id,
            name=student.name,
            class_name=student.class_applied,
            stream=student.faculty,
            allergies=allergies,
        ),
        day=visit.day,
        status=visit.status,
        complaint=visit.complaint,
        note=visit.note,
        sent_by_name=visit.sent_by_name,
        sent_at=visit.sent_at,
        temperature=visit.temperature,
        treatment=visit.treatment,
        nurse_notes=visit.nurse_notes,
        arrived_at=visit.arrived_at,
        closed_at=visit.closed_at,
        closed_by_name=visit.closed_by_name,
    )


async def day_visits(db: AsyncSession, caller: Caller, day: date | None) -> SickBayDay:
    """The day's visits, newest first, plus any still open from earlier days."""
    on = day or caller.today
    rows = (
        await db.execute(
            select(SickBayVisit, Student)
            .join(Student, Student.id == SickBayVisit.student_id)
            .where(
                SickBayVisit.tenant_id == caller.tenant_id,
                (SickBayVisit.day == on)
                | (SickBayVisit.status.in_(OPEN_STATUSES) & (SickBayVisit.day <= on)),
            )
            .order_by(SickBayVisit.sent_at.desc())
        )
    ).all()
    allergies = await _allergy_names(db, caller.tenant_id, {s.id for _, s in rows})
    return SickBayDay(date=on, visits=[_read(v, s, allergies.get(s.id, [])) for v, s in rows])


async def _student(db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID) -> Student:
    await student_access.ensure_in_school(db, tenant_id, student_id)
    student = await db.get(Student, student_id)
    assert student is not None
    return student


async def send(db: AsyncSession, caller: Caller, data: VisitCreate) -> VisitRead:
    student = await _student(db, caller.tenant_id, data.student_id)
    visit = SickBayVisit(
        tenant_id=caller.tenant_id,
        student_id=student.id,
        day=caller.today,
        status="waiting",
        complaint=data.complaint.strip(),
        note=data.note,
        sent_by_name=caller.name,
        sent_at=caller.now,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    allergies = await _allergy_names(db, caller.tenant_id, {student.id})
    return _read(visit, student, allergies.get(student.id, []))


async def update(
    db: AsyncSession, caller: Caller, visit_id: uuid.UUID, data: VisitUpdate
) -> VisitRead:
    visit = await db.scalar(
        select(SickBayVisit).where(
            SickBayVisit.id == visit_id, SickBayVisit.tenant_id == caller.tenant_id
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")
    apply_update(visit, data.model_dump(exclude_unset=True), now=caller.now, by=caller.name)
    await db.commit()
    await db.refresh(visit)
    student = await _student(db, caller.tenant_id, visit.student_id)
    allergies = await _allergy_names(db, caller.tenant_id, {student.id})
    return _read(visit, student, allergies.get(student.id, []))


async def alerts(
    db: AsyncSession, caller: Caller, *, class_name: str | None, stream: str | None
) -> HealthAlerts:
    """Listed students with something on their medical record staff should know, by name."""
    query = (
        select(StudentMedicalProfile, Student)
        .join(Student, Student.id == StudentMedicalProfile.student_id)
        .where(StudentMedicalProfile.tenant_id == caller.tenant_id, _listed())
        .options(
            selectinload(StudentMedicalProfile.allergies),
            selectinload(StudentMedicalProfile.conditions),
            selectinload(StudentMedicalProfile.medications),
        )
        .order_by(Student.name)
    )
    if class_name:
        wanted = class_name.strip().lower()
        query = query.where(func.lower(func.trim(Student.class_applied)) == wanted)
    if stream:
        query = query.where(func.lower(func.trim(Student.faculty)) == stream.strip().lower())
    rows = (await db.execute(query)).all()
    return HealthAlerts(
        alerts=[
            HealthAlert(
                student_id=student.id,
                name=student.name,
                class_name=student.class_applied,
                stream=student.faculty,
                allergies=[
                    AlertAllergy(name=a.name, severity=a.severity, reaction=a.reaction)
                    for a in profile.allergies
                ],
                conditions=[AlertCondition(name=c.name, notes=c.notes) for c in profile.conditions],
                medications=[
                    AlertMedication(name=m.name, dosage=m.dosage, time=m.time)
                    for m in profile.medications
                    if m.is_active
                ],
                emergency_notes=(profile.emergency_notes or "").strip() or None,
            )
            for profile, student in rows
            if has_alert(profile)
        ]
    )
