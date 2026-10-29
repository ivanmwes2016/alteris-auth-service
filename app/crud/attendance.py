"""
Service layer for attendance registers.

A register is a class's daily roll call or one lesson of one subject, on one
school day (a date in the school's own timezone). Students start Unmarked: no
mark row. Teachers, Head Teachers, Admins and the Owner can take any register
in their school; Bursars and Admissions Officers can't.

Marks are free to change until the school day ends. After that any change,
including marking someone who was left Unmarked, is a correction: approvers
(Owner, Head Teacher, Admin) make it directly with a reason, everyone else
sends a correction request that an approver accepts or rejects. A register
taken offline on the day still saves normally if it arrives within 72 hours,
judged by when it was tapped on the phone; the latest tap wins (see
docs/adr/0001 in the marking app repo).

Every query is scoped to the caller's tenant, and every student, subject,
class and lesson id is checked against it: ids from other schools 404.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.crud.academic_term import get_settings as get_term_settings
from app.db.models.academic_term import AcademicTermDates
from app.db.models.academics import SchoolClass, Subject
from app.db.models.attendance import (
    AttendanceMark,
    AttendanceMarkChange,
    AttendanceRegister,
    CorrectionRequest,
    CorrectionRequestItem,
    NoSchoolDay,
)
from app.db.models.role import Role
from app.db.models.staff import Staff
from app.db.models.student import Student, StudentSubject
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.timetable import Lesson, Timetable
from app.db.models.users import User
from app.db.schemas.attendance import (
    CorrectionCreate,
    CorrectionItemRead,
    CorrectionRequestRead,
    CorrectionResult,
    MarkChangeRead,
    MarkRead,
    NoSchoolDayRead,
    PersonRead,
    RegisterCounts,
    RegisterKey,
    RegisterRead,
    RegisterSave,
    RegisterSaveResult,
    RegisterSlot,
    RegisterStudent,
    RegisterSummary,
    SkippedMark,
    TodayRead,
)
from app.helpers.timezones import country_timezone, is_valid_timezone, school_timezone
from app.services.push import send_push, tokens_for

TAKE_REGISTER_ROLES = {"teacher", "head_teacher", "admin"}
APPROVER_ROLES = {"head_teacher", "admin"}
# Student.status is free text; only these are on registers.
LISTED_STUDENT_STATUSES = {"enrolled", "accepted"}

GRACE = timedelta(hours=72)
# Phones' clocks drift a little; a tap this far "in the future" is still accepted.
CLOCK_SKEW = timedelta(minutes=5)
# How far back a correction request can reach when the school has no term dates.
FALLBACK_CORRECTION_DAYS = 120


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def can_take_registers(*, is_owner: bool, role_name: str) -> bool:
    return is_owner or role_name in TAKE_REGISTER_ROLES


def is_approver(*, is_owner: bool, role_name: str) -> bool:
    """Approvers correct past registers directly and decide correction requests."""
    return is_owner or role_name in APPROVER_ROLES


def is_listed(student_status: str | None) -> bool:
    return (student_status or "").strip().lower() in LISTED_STUDENT_STATUSES


def offers_subject(offered: Collection[object], subject_id: object) -> bool:
    """A student with no subjects listed takes every subject their class takes."""
    return not offered or subject_id in offered


def school_today(tz: ZoneInfo, now: datetime) -> date:
    return now.astimezone(tz).date()


def day_end(day: date, tz: ZoneInfo) -> datetime:
    """The moment `day` ends at the school, i.e. its next midnight there."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)


class SaveCheck(Enum):
    OK = "ok"
    FUTURE_REGISTER = "future_register"
    BAD_TAP_TIME = "bad_tap_time"
    LOCKED = "locked"


def check_save(
    *, register_date: date, tapped_at: datetime, now: datetime, tz: ZoneInfo
) -> SaveCheck:
    """
    Whether a mark tapped at `tapped_at` can be saved as a normal edit, not a
    correction. It must be tapped on the register's own school day, and arrive
    before that day ends or within the grace window after.
    """
    today = school_today(tz, now)
    if register_date > today:
        return SaveCheck.FUTURE_REGISTER
    if tapped_at > now + CLOCK_SKEW or tapped_at.astimezone(tz).date() != register_date:
        return SaveCheck.BAD_TAP_TIME
    if register_date == today or now <= day_end(register_date, tz) + GRACE:
        return SaveCheck.OK
    return SaveCheck.LOCKED


def is_newer_tap(existing: datetime | None, incoming: datetime) -> bool:
    """Latest tap wins: an older copy synced late never overwrites a newer mark."""
    return existing is None or incoming >= existing


@dataclass(frozen=True)
class TermSpan:
    label: str
    year: int
    starts_on: date
    ends_on: date


def term_on(day: date, terms: Sequence[TermSpan]) -> TermSpan | None:
    return next((t for t in terms if t.starts_on <= day <= t.ends_on), None)


def correction_window_start(today: date, terms: Sequence[TermSpan]) -> date:
    """
    The earliest date a correction request can reach: the start of the previous
    term (the current one being the latest to have started). With no term dates,
    a fixed number of days back.
    """
    started = sorted((t for t in terms if t.starts_on <= today), key=lambda t: t.starts_on)
    if not started:
        return today - timedelta(days=FALLBACK_CORRECTION_DAYS)
    return started[-2].starts_on if len(started) >= 2 else started[-1].starts_on


def counts_for_gaps(
    day: date, *, today: date, terms: Sequence[TermSpan], closed: set[date]
) -> bool:
    """A finished school day inside a term that wasn't a no-school day."""
    return day < today and day not in closed and term_on(day, terms) is not None


def attendance_rate(*, present: int, late: int, absent: int) -> float | None:
    """(Present + Late) / (Present + Late + Absent); Excused and Unmarked are left out."""
    counted = present + late + absent
    return None if counted == 0 else (present + late) / counted


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


# ---------------------------------------------------------------------------
# Caller context
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Viewer:
    actor: team.Actor
    user_name: str | None
    tz: ZoneInfo
    tz_source: str
    country: str | None
    now: datetime
    today: date
    approver: bool
    staff_id: uuid.UUID | None
    terms: list[TermSpan]

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.actor.tenant_id

    @property
    def user_id(self) -> uuid.UUID:
        return self.actor.user_id


def timezone_source(*, explicit: str | None, country: str | None) -> str:
    if explicit and is_valid_timezone(explicit):
        return "school"
    return "country" if country_timezone(country) else "default"


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


async def load_terms(db: AsyncSession, tenant_id: uuid.UUID) -> list[TermSpan]:
    rows = await db.scalars(
        select(AcademicTermDates)
        .where(AcademicTermDates.tenant_id == tenant_id)
        .order_by(AcademicTermDates.starts_on)
    )
    return [TermSpan(r.label, r.year, r.starts_on, r.ends_on) for r in rows]


async def get_viewer(db: AsyncSession, user: User) -> Viewer:
    actor = await team.get_actor(db, user)
    if not can_take_registers(is_owner=actor.is_owner, role_name=actor.role_name):
        raise _forbidden("Your role can't take attendance")
    tenant = await db.get(Tenant, actor.tenant_id)
    explicit = tenant.timezone if tenant else None
    country = tenant.country if tenant else None
    tz = school_timezone(explicit=explicit, country=country)
    now = now_utc()
    return Viewer(
        actor=actor,
        user_name=user.name,
        tz=tz,
        tz_source=timezone_source(explicit=explicit, country=country),
        country=country,
        now=now,
        today=school_today(tz, now),
        approver=is_approver(is_owner=actor.is_owner, role_name=actor.role_name),
        staff_id=await linked_staff_id(db, tenant_id=actor.tenant_id, user=user),
        terms=await load_terms(db, actor.tenant_id),
    )


def require_approver(viewer: Viewer) -> None:
    if not viewer.approver:
        raise _forbidden("Only the owner, an admin or a head teacher can do this")


async def user_names(
    db: AsyncSession, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str | None]:
    ids = {i for i in user_ids if i is not None}
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
    return {uid: name or email for uid, name, email in rows}


def staff_name(staff: Staff | None) -> str | None:
    if staff is None:
        return None
    return " ".join(p for p in (staff.title, staff.first_name, staff.last_name) if p)


# ---------------------------------------------------------------------------
# Which register a key means
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    kind: str
    date: date
    class_name: str
    # Daily registers: only narrows the list. Lesson registers: part of the register.
    stream: str | None
    subject_id: uuid.UUID | None = None
    subject_name: str | None = None
    lesson_id: uuid.UUID | None = None
    starts_at: time | None = None
    ends_at: time | None = None
    staff_id: uuid.UUID | None = None
    teacher_name: str | None = None

    def key(self) -> RegisterKey:
        if self.kind == "daily":
            return RegisterKey(
                kind="daily", date=self.date, class_name=self.class_name, stream=self.stream
            )
        if self.lesson_id is not None:
            return RegisterKey(kind="lesson", date=self.date, lesson_id=self.lesson_id)
        return RegisterKey(
            kind="lesson",
            date=self.date,
            class_name=self.class_name,
            stream=self.stream,
            subject_id=self.subject_id,
            starts_at=self.starts_at,
        )


async def _require_class(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, stream: str | None
) -> None:
    query = select(SchoolClass.id).where(
        SchoolClass.tenant_id == tenant_id, SchoolClass.name == name
    )
    if stream is not None:
        query = query.where(SchoolClass.stream == stream)
    if await db.scalar(query.limit(1)) is None:
        raise _not_found("Class")


async def timetable_on(db: AsyncSession, viewer: Viewer, day: date) -> Timetable | None:
    """
    The timetable that applies on `day`: its term's, by the school's term dates.
    A school without term dates uses its current term's timetable every day.
    """
    term = term_on(day, viewer.terms)
    if term is not None:
        label, year = term.label, term.year
    elif not viewer.terms:
        settings = await get_term_settings(db, tenant_id=viewer.tenant_id)
        if settings is None or settings.current_label is None or settings.current_year is None:
            return None
        label, year = settings.current_label, settings.current_year
    else:
        return None  # between terms
    timetable: Timetable | None = await db.scalar(
        select(Timetable).where(
            Timetable.tenant_id == viewer.tenant_id, Timetable.term == label, Timetable.year == year
        )
    )
    return timetable


def lesson_end(timetable: Timetable, lesson: Lesson) -> time | None:
    ids = [p.id for p in timetable.periods]
    if lesson.period_id not in ids:
        return None
    last = min(ids.index(lesson.period_id) + lesson.length - 1, len(ids) - 1)
    return timetable.periods[last].ends_at


async def lesson_spec(
    db: AsyncSession, viewer: Viewer, day: date, lesson: Lesson, timetable: Timetable
) -> Spec:
    school_class = await db.get(SchoolClass, lesson.class_id)
    subject = await db.get(Subject, lesson.subject_id)
    staff = await db.get(Staff, lesson.staff_id) if lesson.staff_id else None
    start = next((p.starts_at for p in timetable.periods if p.id == lesson.period_id), None)
    return Spec(
        kind="lesson",
        date=day,
        class_name=school_class.name if school_class else "",
        stream=school_class.stream if school_class else None,
        subject_id=lesson.subject_id,
        subject_name=subject.name if subject else None,
        lesson_id=lesson.id,
        starts_at=start,
        ends_at=lesson_end(timetable, lesson),
        staff_id=lesson.staff_id,
        teacher_name=staff_name(staff),
    )


async def resolve(db: AsyncSession, viewer: Viewer, key: RegisterKey) -> Spec:
    """Checks every id in the key against the caller's school."""
    if key.kind == "daily":
        assert key.class_name is not None
        await _require_class(db, viewer.tenant_id, key.class_name, key.stream)
        return Spec(kind="daily", date=key.date, class_name=key.class_name, stream=key.stream)

    if key.lesson_id is not None:
        lesson = await db.scalar(
            select(Lesson).where(Lesson.id == key.lesson_id, Lesson.tenant_id == viewer.tenant_id)
        )
        if lesson is None:
            raise _not_found("Lesson")
        timetable = await timetable_on(db, viewer, key.date)
        if (
            timetable is None
            or timetable.id != lesson.timetable_id
            or lesson.day != key.date.weekday()
        ):
            raise invalid(f"That lesson isn't on the timetable on {_day(key.date)}")
        return await lesson_spec(db, viewer, key.date, lesson, timetable)

    assert key.class_name is not None and key.subject_id is not None
    await _require_class(db, viewer.tenant_id, key.class_name, key.stream)
    subject = await db.scalar(
        select(Subject).where(Subject.id == key.subject_id, Subject.tenant_id == viewer.tenant_id)
    )
    if subject is None:
        raise _not_found("Subject")
    return Spec(
        kind="lesson",
        date=key.date,
        class_name=key.class_name,
        stream=key.stream,
        subject_id=subject.id,
        subject_name=subject.name,
        starts_at=key.starts_at,
    )


def _register_query(viewer: Viewer, spec: Spec) -> Select[tuple[AttendanceRegister]]:
    query = select(AttendanceRegister).where(
        AttendanceRegister.tenant_id == viewer.tenant_id, AttendanceRegister.date == spec.date
    )
    if spec.kind == "daily":
        return query.where(
            AttendanceRegister.kind == "daily", AttendanceRegister.class_name == spec.class_name
        )
    if spec.lesson_id is not None:
        return query.where(AttendanceRegister.lesson_id == spec.lesson_id)
    stream = (
        AttendanceRegister.stream.is_(None)
        if spec.stream is None
        else AttendanceRegister.stream == spec.stream
    )
    return query.where(
        AttendanceRegister.kind == "lesson",
        AttendanceRegister.lesson_id.is_(None),
        AttendanceRegister.class_name == spec.class_name,
        stream,
        AttendanceRegister.subject_id == spec.subject_id,
        AttendanceRegister.starts_at == spec.starts_at,
    )


async def find_register(db: AsyncSession, viewer: Viewer, spec: Spec) -> AttendanceRegister | None:
    register: AttendanceRegister | None = await db.scalar(_register_query(viewer, spec))
    return register


async def get_or_create_register(
    db: AsyncSession, viewer: Viewer, spec: Spec
) -> AttendanceRegister:
    """ON CONFLICT DO NOTHING: two teachers opening the same register get one row."""
    register = await find_register(db, viewer, spec)
    if register is not None:
        return register
    await db.execute(
        pg_insert(AttendanceRegister)
        .values(
            id=uuid.uuid4(),
            tenant_id=viewer.tenant_id,
            kind=spec.kind,
            date=spec.date,
            class_name=spec.class_name,
            stream=spec.stream if spec.kind == "lesson" else None,
            subject_id=spec.subject_id,
            subject_name=spec.subject_name,
            lesson_id=spec.lesson_id,
            starts_at=spec.starts_at,
            staff_id=spec.staff_id,
            created_by=viewer.user_id,
        )
        .on_conflict_do_nothing()
    )
    await db.flush()
    register = await find_register(db, viewer, spec)
    if register is None:
        raise RuntimeError(f"Failed to get or create the register for {spec}")
    return register


# ---------------------------------------------------------------------------
# Who's on a register
# ---------------------------------------------------------------------------
def _listed_filter() -> ColumnElement[bool]:
    return func.lower(func.trim(Student.status)).in_(LISTED_STUDENT_STATUSES)


async def subjects_offered(
    db: AsyncSession, student_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    ids = list(student_ids)
    offered: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if ids:
        rows = await db.execute(
            select(StudentSubject.student_id, StudentSubject.subject_id).where(
                StudentSubject.student_id.in_(ids)
            )
        )
        for student_id, subject_id in rows:
            offered[student_id].add(subject_id)
    return offered


async def roster(db: AsyncSession, viewer: Viewer, spec: Spec) -> list[Student]:
    """The class's enrolled students (in the stream, if any) who offer the lesson's subject."""
    query = (
        select(Student)
        .where(
            Student.tenant_id == viewer.tenant_id,
            Student.class_applied == spec.class_name,
            _listed_filter(),
        )
        .order_by(Student.name, Student.id)
    )
    if spec.stream is not None:
        query = query.where(Student.faculty == spec.stream)
    students = list(await db.scalars(query))
    if spec.kind == "lesson" and spec.subject_id is not None:
        offered = await subjects_offered(db, [s.id for s in students])
        students = [
            s for s in students if offers_subject(offered.get(s.id, set()), spec.subject_id)
        ]
    return students


# ---------------------------------------------------------------------------
# Reading a register
# ---------------------------------------------------------------------------
_COUNT_FIELD = {
    "Present": "present",
    "Late": "late",
    "Unexcused": "unexcused",
    "Excused": "excused",
}


def register_state(day: date, today: date) -> str:
    if day > today:
        return "future"
    return "open" if day == today else "locked"


async def _pending_students(db: AsyncSession, register_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(CorrectionRequestItem.student_id)
        .join(CorrectionRequest, CorrectionRequest.id == CorrectionRequestItem.request_id)
        .where(CorrectionRequest.register_id == register_id, CorrectionRequest.status == "pending")
    )
    return set(rows)


async def read_register(
    db: AsyncSession,
    viewer: Viewer,
    spec: Spec,
    register: AttendanceRegister | None,
    search: str | None = None,
) -> RegisterRead:
    students = await roster(db, viewer, spec)
    marks: dict[uuid.UUID, AttendanceMark] = {}
    pending: set[uuid.UUID] = set()
    if register is not None:
        await db.refresh(register, ["marks"])
        marks = {m.student_id: m for m in register.marks}
        pending = await _pending_students(db, register.id)
        # Students marked here who have since left the list (moved class, left school).
        listed = {s.id for s in students}
        # A daily register's stream is only a filter: keep it for them too.
        gone = [
            sid
            for sid, mark in marks.items()
            if sid not in listed
            and (spec.kind != "daily" or spec.stream is None or mark.stream == spec.stream)
        ]
        if gone:
            students += list(
                await db.scalars(
                    select(Student)
                    .where(Student.id.in_(gone), Student.tenant_id == viewer.tenant_id)
                    .order_by(Student.name)
                )
            )
    names = await user_names(db, (m.marked_by for m in marks.values()))

    counts = RegisterCounts()
    rows: list[RegisterStudent] = []
    needle = (search or "").strip().lower()
    for student in students:
        mark = marks.get(student.id)
        if mark is None:
            counts.unmarked += 1
        else:
            field = _COUNT_FIELD.get(mark.status)
            if field:
                setattr(counts, field, getattr(counts, field) + 1)
        if (
            needle
            and needle not in student.name.lower()
            and needle not in (student.school_id or "").lower()
        ):
            continue
        rows.append(
            RegisterStudent(
                student_id=student.id,
                name=student.name,
                admission_number=student.school_id,
                stream=student.faculty,
                photo=student.photo,
                mark=MarkRead(
                    status=mark.status,
                    note=mark.note,
                    marked_by_name=names.get(mark.marked_by) if mark.marked_by else None,
                    tapped_at=mark.tapped_at,
                )
                if mark
                else None,
                pending_correction=student.id in pending,
            )
        )

    state = register_state(spec.date, viewer.today)
    return RegisterRead(
        register_id=register.id if register else None,
        key=spec.key(),
        kind=spec.kind,
        date=spec.date,
        class_name=spec.class_name,
        stream=spec.stream,
        subject_id=spec.subject_id,
        subject_name=spec.subject_name,
        lesson_id=spec.lesson_id,
        starts_at=spec.starts_at,
        ends_at=spec.ends_at,
        teacher_name=spec.teacher_name,
        state=state,
        can_edit=state == "open",
        can_correct=state == "locked" and viewer.approver,
        can_request_correction=state == "locked"
        and not viewer.approver
        and spec.date >= correction_window_start(viewer.today, viewer.terms),
        counts=counts,
        students=rows,
    )


async def get_register(
    db: AsyncSession, viewer: Viewer, key: RegisterKey, search: str | None
) -> RegisterRead:
    spec = await resolve(db, viewer, key)
    return await read_register(db, viewer, spec, await find_register(db, viewer, spec), search)


# ---------------------------------------------------------------------------
# Changing marks
# ---------------------------------------------------------------------------
def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _apply_mark(
    db: AsyncSession,
    viewer: Viewer,
    register: AttendanceRegister,
    student: Student,
    existing: AttendanceMark | None,
    *,
    status_: str,
    note: str | None,
    tapped_at: datetime,
    reason: str | None = None,
    request_id: uuid.UUID | None = None,
) -> AttendanceMark:
    """Sets one mark and records the change. The caller commits."""
    if existing is None:
        mark = AttendanceMark(
            id=uuid.uuid4(),
            tenant_id=viewer.tenant_id,
            register_id=register.id,
            student_id=student.id,
            status=status_,
            note=note,
            class_name=student.class_applied or register.class_name,
            stream=student.faculty,
            marked_by=viewer.user_id,
            tapped_at=tapped_at,
        )
        db.add(mark)
        from_status, from_note = None, None
    else:
        mark = existing
        from_status, from_note = mark.status, mark.note
        mark.status, mark.note = status_, note
        mark.marked_by, mark.tapped_at = viewer.user_id, tapped_at
    db.add(
        AttendanceMarkChange(
            tenant_id=viewer.tenant_id,
            mark_id=mark.id,
            from_status=from_status,
            to_status=status_,
            from_note=from_note,
            to_note=note,
            changed_by=viewer.user_id,
            tapped_at=tapped_at,
            reason=reason,
            correction_request_id=request_id,
        )
    )
    return mark


async def _markable_students(
    db: AsyncSession, viewer: Viewer, spec: Spec, register: AttendanceRegister
) -> dict[uuid.UUID, Student]:
    """The register's list, plus anyone already marked on it."""
    students = {s.id: s for s in await roster(db, viewer, spec)}
    marked = [m.student_id for m in register.marks if m.student_id not in students]
    if marked:
        for student in await db.scalars(
            select(Student).where(Student.id.in_(marked), Student.tenant_id == viewer.tenant_id)
        ):
            students[student.id] = student
    return students


async def save_marks(db: AsyncSession, viewer: Viewer, payload: RegisterSave) -> RegisterSaveResult:
    """
    Same-day marks, or a register taken offline on its day that syncs within the
    grace window. All or nothing on timing: a locked register needs a correction.
    """
    spec = await resolve(db, viewer, payload.register_)
    taps = [_aware(m.tapped_at) if m.tapped_at else viewer.now for m in payload.marks]
    checks = {
        check_save(register_date=spec.date, tapped_at=t, now=viewer.now, tz=viewer.tz) for t in taps
    }
    if SaveCheck.FUTURE_REGISTER in checks:
        raise invalid(f"{_day(spec.date)}'s register can't be taken yet")
    if SaveCheck.LOCKED in checks:
        raise _conflict(f"{_day(spec.date)}'s register has closed. Send a correction instead.")
    if SaveCheck.BAD_TAP_TIME in checks:
        raise _conflict(
            f"These marks weren't taken on {_day(spec.date)}. Send a correction instead."
        )

    register = await get_or_create_register(db, viewer, spec)
    await db.refresh(register, ["marks"])
    students = await _markable_students(db, viewer, spec, register)
    existing = {m.student_id: m for m in register.marks}
    skipped: list[SkippedMark] = []
    for mark_in, tapped in zip(payload.marks, taps, strict=True):
        student = students.get(mark_in.student_id)
        if student is None:
            skipped.append(SkippedMark(student_id=mark_in.student_id, reason="not_on_register"))
            continue
        current = existing.get(student.id)
        if current is not None and not is_newer_tap(current.tapped_at, tapped):
            skipped.append(SkippedMark(student_id=student.id, reason="newer_mark"))
            continue
        if current is not None and (current.status, current.note) == (mark_in.status, mark_in.note):
            continue
        existing[student.id] = _apply_mark(
            db,
            viewer,
            register,
            student,
            current,
            status_=mark_in.status,
            note=mark_in.note,
            tapped_at=tapped,
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict("Someone saved this register at the same moment. Try again.") from exc
    return RegisterSaveResult(
        register_=await read_register(db, viewer, spec, register), skipped=skipped
    )


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------
def _register_title(register: AttendanceRegister) -> str:
    who = " ".join(p for p in (register.class_name, register.stream) if p)
    return (
        f"{register.subject_name} for {who}" if register.kind == "lesson" else f"{who}'s register"
    )


async def approver_user_ids(db: AsyncSession, tenant_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(TenantMember.user_id)
        .join(Role, Role.id == TenantMember.role_id)
        .where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.status == "active",
            Role.name.in_(APPROVER_ROLES),
        )
    )
    ids = set(rows)
    owner_id = await db.scalar(select(Tenant.owner_id).where(Tenant.id == tenant_id))
    if owner_id is not None:
        ids.add(owner_id)
    return ids


async def _request_read(db: AsyncSession, request: CorrectionRequest) -> CorrectionRequestRead:
    register = request.register
    names = await user_names(db, (request.requested_by, request.decided_by))
    rows = await db.execute(
        select(Student.id, Student.name).where(
            Student.id.in_([i.student_id for i in request.items])
        )
    )
    student_names: dict[uuid.UUID, str] = dict(rows.tuples().all())
    return CorrectionRequestRead(
        id=request.id,
        status=request.status,
        reason=request.reason,
        register_=RegisterSummary(
            register_id=register.id,
            kind=register.kind,
            date=register.date,
            class_name=register.class_name,
            stream=register.stream,
            subject_name=register.subject_name,
            starts_at=register.starts_at,
        ),
        requested_by=PersonRead(
            id=request.requested_by,
            name=names.get(request.requested_by) if request.requested_by else None,
        ),
        created_at=request.created_at,
        decided_by=PersonRead(id=request.decided_by, name=names.get(request.decided_by))
        if request.decided_by
        else None,
        decided_at=request.decided_at,
        decision_note=request.decision_note,
        items=[
            CorrectionItemRead(
                student_id=item.student_id,
                student_name=student_names.get(item.student_id, ""),
                from_status=item.from_status,
                to_status=item.to_status,
                note=item.note,
            )
            for item in request.items
        ],
    )


async def correct(
    db: AsyncSession, viewer: Viewer, payload: CorrectionCreate, background: BackgroundTasks
) -> CorrectionResult:
    """
    Changes a past register. Approvers' corrections apply at once; anyone else's
    becomes a correction request and the marks stay as they are until it's accepted.
    """
    spec = await resolve(db, viewer, payload.register_)
    if spec.date >= viewer.today:
        raise invalid("Today's register can still be changed directly")
    window = correction_window_start(viewer.today, viewer.terms)
    if not viewer.approver and spec.date < window:
        raise invalid(f"Registers before {_day(window)} can only be corrected by an approver")

    register = await get_or_create_register(db, viewer, spec)
    await db.refresh(register, ["marks"])
    students = await _markable_students(db, viewer, spec, register)
    if any(c.student_id not in students for c in payload.changes):
        raise invalid("Some of these students aren't on this register")
    existing = {m.student_id: m for m in register.marks}
    changes = [
        c
        for c in payload.changes
        if (current := existing.get(c.student_id)) is None
        or (current.status, current.note) != (c.status, c.note)
    ]
    if not changes:
        raise invalid("Nothing to change: the register already shows this")

    if viewer.approver:
        for change in changes:
            _apply_mark(
                db,
                viewer,
                register,
                students[change.student_id],
                existing.get(change.student_id),
                status_=change.status,
                note=change.note,
                tapped_at=viewer.now,
                reason=payload.reason,
            )
        await db.commit()
        return CorrectionResult(
            applied=True, register_=await read_register(db, viewer, spec, register)
        )

    request = CorrectionRequest(
        id=uuid.uuid4(),
        tenant_id=viewer.tenant_id,
        register_id=register.id,
        requested_by=viewer.user_id,
        reason=payload.reason,
        status="pending",
        items=[
            CorrectionRequestItem(
                student_id=c.student_id,
                from_status=existing[c.student_id].status if c.student_id in existing else None,
                to_status=c.status,
                note=c.note,
            )
            for c in changes
        ],
    )
    db.add(request)
    await db.commit()
    await db.refresh(request, ["register", "items", "created_at"])

    approvers = await approver_user_ids(db, viewer.tenant_id) - {viewer.user_id}
    background.add_task(
        send_push,
        await tokens_for(db, list(approvers)),
        title="Correction request",
        body=f"{viewer.user_name or 'A teacher'} asked to correct {len(changes)} "
        f"mark{'s' if len(changes) != 1 else ''} on {_register_title(register)}, "
        f"{_day(register.date)}",
        data={"type": "attendance_correction_request", "requestId": str(request.id)},
    )
    return CorrectionResult(
        applied=False,
        request=await _request_read(db, request),
        register_=await read_register(db, viewer, spec, register),
    )


def _requests_query(viewer: Viewer) -> Select[tuple[CorrectionRequest]]:
    return select(CorrectionRequest).where(CorrectionRequest.tenant_id == viewer.tenant_id)


async def list_requests(
    db: AsyncSession, viewer: Viewer, *, scope: str, status_: str | None
) -> list[CorrectionRequestRead]:
    """`mine`: the caller's own requests. `review`: everyone's, for approvers."""
    query = _requests_query(viewer)
    if scope == "review":
        require_approver(viewer)
    else:
        query = query.where(CorrectionRequest.requested_by == viewer.user_id)
    if status_:
        query = query.where(CorrectionRequest.status == status_)
    requests = await db.scalars(query.order_by(CorrectionRequest.created_at.desc()).limit(200))
    return [await _request_read(db, r) for r in requests]


async def _get_request(
    db: AsyncSession, viewer: Viewer, request_id: uuid.UUID, *, lock: bool = False
) -> CorrectionRequest:
    query = _requests_query(viewer).where(CorrectionRequest.id == request_id)
    if not viewer.approver:
        query = query.where(CorrectionRequest.requested_by == viewer.user_id)
    if lock:
        query = query.with_for_update()
    request = await db.scalar(query)
    if request is None:
        raise _not_found("Correction request")
    return request


async def get_request(
    db: AsyncSession, viewer: Viewer, request_id: uuid.UUID
) -> CorrectionRequestRead:
    return await _request_read(db, await _get_request(db, viewer, request_id))


async def decide(
    db: AsyncSession,
    viewer: Viewer,
    request_id: uuid.UUID,
    *,
    accept: bool,
    note: str | None,
    background: BackgroundTasks,
) -> CorrectionRequestRead:
    """The first approver to act settles it; the row lock stops a second decision."""
    require_approver(viewer)
    request = await _get_request(db, viewer, request_id, lock=True)
    if request.status != "pending":
        names = await user_names(db, [request.decided_by])
        by = names.get(request.decided_by) if request.decided_by else None
        raise _conflict(f"This request was already {request.status}" + (f" by {by}" if by else ""))

    if accept:
        register = request.register
        await db.refresh(register, ["marks"])
        existing = {m.student_id: m for m in register.marks}
        students = {
            s.id: s
            for s in await db.scalars(
                select(Student).where(
                    Student.id.in_([i.student_id for i in request.items]),
                    Student.tenant_id == viewer.tenant_id,
                )
            )
        }
        for item in request.items:
            student = students.get(item.student_id)
            if student is None:
                continue
            existing[student.id] = _apply_mark(
                db,
                viewer,
                register,
                student,
                existing.get(student.id),
                status_=item.to_status,
                note=item.note,
                tapped_at=viewer.now,
                reason=request.reason,
                request_id=request.id,
            )
    request.status = "accepted" if accept else "rejected"
    request.decided_by = viewer.user_id
    request.decided_at = viewer.now
    request.decision_note = note
    await db.commit()
    await db.refresh(request, ["register", "items"])

    if request.requested_by is not None:
        outcome = "accepted" if accept else "rejected"
        body = f"{viewer.user_name or 'An approver'} {outcome} your correction to " + (
            f"{_register_title(request.register)}, {_day(request.register.date)}"
        )
        if note:
            body += f": {note}"
        background.add_task(
            send_push,
            await tokens_for(db, [request.requested_by]),
            title=f"Correction {outcome}",
            body=body,
            data={"type": "attendance_correction_decided", "requestId": str(request.id)},
        )
    return await _request_read(db, request)


async def mark_history(
    db: AsyncSession, viewer: Viewer, register_id: uuid.UUID, student_id: uuid.UUID
) -> list[MarkChangeRead]:
    mark = await db.scalar(
        select(AttendanceMark).where(
            AttendanceMark.register_id == register_id,
            AttendanceMark.student_id == student_id,
            AttendanceMark.tenant_id == viewer.tenant_id,
        )
    )
    if mark is None:
        return []
    changes = list(
        await db.scalars(
            select(AttendanceMarkChange)
            .where(AttendanceMarkChange.mark_id == mark.id)
            .order_by(AttendanceMarkChange.changed_at, AttendanceMarkChange.id)
        )
    )
    names = await user_names(db, (c.changed_by for c in changes))
    return [
        MarkChangeRead(
            from_status=c.from_status,
            to_status=c.to_status,
            from_note=c.from_note,
            to_note=c.to_note,
            changed_by=PersonRead(
                id=c.changed_by, name=names.get(c.changed_by) if c.changed_by else None
            ),
            changed_at=c.changed_at,
            tapped_at=c.tapped_at,
            reason=c.reason,
            correction_request_id=c.correction_request_id,
        )
        for c in changes
    ]


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SchoolRoster:
    """Every enrolled student in the school, to size many registers in one query."""

    students: list[Student]
    offered: dict[uuid.UUID, set[uuid.UUID]]

    def ids(
        self, class_name: str, stream: str | None, subject_id: uuid.UUID | None
    ) -> set[uuid.UUID]:
        return {
            s.id
            for s in self.students
            if s.class_applied == class_name
            and (stream is None or s.faculty == stream)
            and (subject_id is None or offers_subject(self.offered.get(s.id, set()), subject_id))
        }


async def school_roster(db: AsyncSession, tenant_id: uuid.UUID) -> SchoolRoster:
    students = list(
        await db.scalars(select(Student).where(Student.tenant_id == tenant_id, _listed_filter()))
    )
    return SchoolRoster(students, await subjects_offered(db, [s.id for s in students]))


def _marked(register: AttendanceRegister | None, ids: set[uuid.UUID]) -> int:
    return 0 if register is None else sum(1 for m in register.marks if m.student_id in ids)


async def get_today(db: AsyncSession, viewer: Viewer, day: date | None) -> TodayRead:
    day = day or viewer.today
    closed = await db.scalar(
        select(NoSchoolDay).where(
            NoSchoolDay.tenant_id == viewer.tenant_id, NoSchoolDay.date == day
        )
    )
    everyone = await school_roster(db, viewer.tenant_id)
    registers = list(
        await db.scalars(
            select(AttendanceRegister).where(
                AttendanceRegister.tenant_id == viewer.tenant_id, AttendanceRegister.date == day
            )
        )
    )
    by_lesson = {r.lesson_id: r for r in registers if r.lesson_id}
    daily_by_class = {r.class_name: r for r in registers if r.kind == "daily"}

    lessons: list[RegisterSlot] = []
    mine: list[RegisterSlot] = []
    timetable = await timetable_on(db, viewer, day)
    if timetable is not None:
        starts = {p.id: (p.position, p.starts_at) for p in timetable.periods}
        todays = sorted(
            (lesson for lesson in timetable.lessons if lesson.day == day.weekday()),
            key=lambda lesson: starts.get(lesson.period_id, (0, time.min)),
        )
        for lesson in todays:
            spec = await lesson_spec(db, viewer, day, lesson, timetable)
            ids = everyone.ids(spec.class_name, spec.stream, spec.subject_id)
            slot = RegisterSlot(
                key=spec.key(),
                kind="lesson",
                class_name=spec.class_name,
                stream=spec.stream,
                subject_name=spec.subject_name,
                starts_at=spec.starts_at,
                ends_at=spec.ends_at,
                teacher_name=spec.teacher_name,
                marked=_marked(by_lesson.get(lesson.id), ids),
                total=len(ids),
            )
            lessons.append(slot)
            if viewer.staff_id is not None and lesson.staff_id == viewer.staff_id:
                mine.append(slot)

    class_names = sorted(
        set(
            await db.scalars(
                select(SchoolClass.name).where(SchoolClass.tenant_id == viewer.tenant_id).distinct()
            )
        )
    )
    daily = []
    for name in class_names:
        ids = everyone.ids(name, None, None)
        spec = Spec(kind="daily", date=day, class_name=name, stream=None)
        daily.append(
            RegisterSlot(
                key=spec.key(),
                kind="daily",
                class_name=name,
                marked=_marked(daily_by_class.get(name), ids),
                total=len(ids),
            )
        )

    adhoc = []
    for register in sorted(
        (r for r in registers if r.kind == "lesson" and r.lesson_id is None),
        key=lambda r: (r.starts_at or time.min, r.class_name),
    ):
        spec = Spec(
            kind="lesson",
            date=day,
            class_name=register.class_name,
            stream=register.stream,
            subject_id=register.subject_id,
            subject_name=register.subject_name,
            starts_at=register.starts_at,
        )
        ids = everyone.ids(register.class_name, register.stream, register.subject_id)
        slot = RegisterSlot(
            key=spec.key(),
            kind="lesson",
            class_name=register.class_name,
            stream=register.stream,
            subject_name=register.subject_name,
            starts_at=register.starts_at,
            marked=_marked(register, ids),
            total=len(ids),
        )
        adhoc.append(slot)
        if register.created_by == viewer.user_id:
            mine.append(slot)

    return TodayRead(
        date=day,
        today=viewer.today,
        timezone=viewer.tz.key,
        no_school=NoSchoolDayRead.model_validate(closed) if closed else None,
        my_lessons=sorted(mine, key=lambda s: s.starts_at or time.min),
        daily=daily,
        lessons=lessons,
        adhoc=adhoc,
    )


# ---------------------------------------------------------------------------
# Whole-day records, in the shape the old per-student attendance used
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DayRecord:
    id: uuid.UUID
    date: date
    status: str
    note: str | None
    created_at: datetime


async def daily_records(
    db: AsyncSession, tenant_id: uuid.UUID, student_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[DayRecord]]:
    """Each student's daily-register marks, oldest first."""
    records: dict[uuid.UUID, list[DayRecord]] = defaultdict(list)
    if not student_ids:
        return records
    rows = await db.execute(
        select(AttendanceMark, AttendanceRegister.date)
        .join(AttendanceRegister, AttendanceRegister.id == AttendanceMark.register_id)
        .where(
            AttendanceMark.tenant_id == tenant_id,
            AttendanceMark.student_id.in_(student_ids),
            AttendanceRegister.kind == "daily",
        )
        .order_by(AttendanceRegister.date)
    )
    for mark, day in rows.tuples():
        records[mark.student_id].append(
            DayRecord(
                id=mark.id, date=day, status=mark.status, note=mark.note, created_at=mark.tapped_at
            )
        )
    return records
