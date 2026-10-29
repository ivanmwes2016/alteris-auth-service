"""
Attendance reports: rates for the school and for one student, and register gaps.

Daily and lesson attendance are always reported separately: "did they come to
school?" and "did they go to class?" are different questions. Rates come from
the marks as taken, with the class and stream they were taken in, so
promotions don't rewrite past attendance.

A register gap is a student left Unmarked on a finished register. It counts
against the timetabled teacher (the creator, for ad-hoc lessons), and only on
term days that weren't no-school days. Approvers see every gap; teachers see
their own. Daily registers only count for classes that take them that term,
so schools that only mark lessons aren't flooded with daily gaps.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.attendance import (
    Spec,
    TermSpan,
    Viewer,
    attendance_rate,
    counts_for_gaps,
    lesson_spec,
    school_roster,
    staff_name,
    term_on,
    timetable_on,
    user_names,
)
from app.db.models.attendance import AttendanceMark, AttendanceRegister, NoSchoolDay
from app.db.models.staff import Staff
from app.db.models.student import Student
from app.db.schemas.attendance import (
    ClassRates,
    GapRead,
    GapsRead,
    OverviewRead,
    PersonRead,
    RateRead,
    StudentMarkRead,
    StudentReportRead,
    SubjectRate,
)

MAX_RANGE = timedelta(days=400)
RECENT_LIMIT = 30


def check_range(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The end date is before the start"
        )
    if end - start > MAX_RANGE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a range of at most 400 days"
        )


@dataclass
class Tally:
    present: int = 0
    late: int = 0
    unexcused: int = 0
    excused: int = 0

    def add(self, status_: str, count: int = 1) -> None:
        field = status_.lower()
        if hasattr(self, field):
            setattr(self, field, getattr(self, field) + count)

    def read(self) -> RateRead:
        return RateRead(
            present=self.present,
            late=self.late,
            unexcused=self.unexcused,
            excused=self.excused,
            rate=attendance_rate(present=self.present, late=self.late, absent=self.unexcused),
        )


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------
async def overview(
    db: AsyncSession,
    viewer: Viewer,
    *,
    start: date,
    end: date,
    class_name: str | None,
    stream: str | None,
) -> OverviewRead:
    check_range(start, end)
    query = (
        select(
            AttendanceRegister.kind,
            AttendanceMark.class_name,
            AttendanceMark.stream,
            AttendanceMark.status,
        )
        .join(AttendanceRegister, AttendanceRegister.id == AttendanceMark.register_id)
        .where(
            AttendanceMark.tenant_id == viewer.tenant_id,
            AttendanceRegister.date.between(start, end),
        )
    )
    if class_name:
        query = query.where(AttendanceMark.class_name == class_name)
    if stream:
        query = query.where(AttendanceMark.stream == stream)

    totals = {"daily": Tally(), "lesson": Tally()}
    by_class: dict[tuple[str, str | None], dict[str, Tally]] = defaultdict(
        lambda: {"daily": Tally(), "lesson": Tally()}
    )
    for kind, mark_class, mark_stream, mark_status in await db.execute(query):
        totals[kind].add(mark_status)
        by_class[(mark_class, mark_stream)][kind].add(mark_status)

    classes = [
        ClassRates(
            class_name=name,
            stream=class_stream,
            daily=t["daily"].read(),
            lessons=t["lesson"].read(),
        )
        for (name, class_stream), t in by_class.items()
    ]
    # Lowest attendance first; classes with no rate yet go last.
    classes.sort(
        key=lambda c: (
            c.daily.rate if c.daily.rate is not None else 2,
            c.lessons.rate if c.lessons.rate is not None else 2,
            c.class_name,
        )
    )
    return OverviewRead(
        start=start,
        end=end,
        daily=totals["daily"].read(),
        lessons=totals["lesson"].read(),
        classes=classes,
    )


async def student_report(
    db: AsyncSession, viewer: Viewer, student_id: uuid.UUID, *, start: date, end: date
) -> StudentReportRead:
    check_range(start, end)
    student = await db.scalar(
        select(Student).where(Student.id == student_id, Student.tenant_id == viewer.tenant_id)
    )
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")

    rows = await db.execute(
        select(AttendanceRegister, AttendanceMark)
        .join(AttendanceMark, AttendanceMark.register_id == AttendanceRegister.id)
        .where(
            AttendanceMark.student_id == student.id,
            AttendanceMark.tenant_id == viewer.tenant_id,
            AttendanceRegister.date.between(start, end),
        )
        .order_by(AttendanceRegister.date.desc(), AttendanceRegister.starts_at.desc())
    )
    daily, lessons = Tally(), Tally()
    subjects: dict[str, Tally] = defaultdict(Tally)
    recent: list[StudentMarkRead] = []
    for register, mark in rows.tuples():
        if register.kind == "daily":
            daily.add(mark.status)
        else:
            lessons.add(mark.status)
            subjects[register.subject_name or "Unknown subject"].add(mark.status)
        if mark.status != "Present" and len(recent) < RECENT_LIMIT:
            recent.append(
                StudentMarkRead(
                    date=register.date,
                    kind=register.kind,
                    subject_name=register.subject_name,
                    starts_at=register.starts_at,
                    status=mark.status,
                    note=mark.note,
                )
            )
    return StudentReportRead(
        student_id=student.id,
        name=student.name,
        admission_number=student.school_id,
        class_name=student.class_applied,
        stream=student.faculty,
        start=start,
        end=end,
        daily=daily.read(),
        lessons=lessons.read(),
        subjects=[
            SubjectRate(subject_name=name, rates=t.read()) for name, t in sorted(subjects.items())
        ],
        recent=recent,
    )


# ---------------------------------------------------------------------------
# Register gaps
# ---------------------------------------------------------------------------
async def _daily_classes_by_term(
    db: AsyncSession, viewer: Viewer, terms: list[TermSpan]
) -> dict[tuple[str, int], set[str]]:
    """Which classes took any daily register in each term."""
    if not terms:
        return {}
    used: dict[tuple[str, int], set[str]] = defaultdict(set)
    rows = await db.execute(
        select(AttendanceRegister.class_name, AttendanceRegister.date)
        .where(
            AttendanceRegister.tenant_id == viewer.tenant_id,
            AttendanceRegister.kind == "daily",
            AttendanceRegister.date.between(
                min(t.starts_on for t in terms), max(t.ends_on for t in terms)
            ),
        )
        .distinct()
    )
    for name, day in rows:
        term = term_on(day, terms)
        if term is not None:
            used[(term.label, term.year)].add(name)
    return used


async def gaps(db: AsyncSession, viewer: Viewer, *, start: date, end: date) -> GapsRead:
    check_range(start, end)
    if not viewer.terms:
        return GapsRead(start=start, end=end, needs_term_dates=True, gaps=[])

    closed = set(
        await db.scalars(
            select(NoSchoolDay.date).where(
                NoSchoolDay.tenant_id == viewer.tenant_id, NoSchoolDay.date.between(start, end)
            )
        )
    )
    days = [
        start + timedelta(days=n)
        for n in range((end - start).days + 1)
        if counts_for_gaps(
            start + timedelta(days=n), today=viewer.today, terms=viewer.terms, closed=closed
        )
    ]
    if not days:
        return GapsRead(start=start, end=end, needs_term_dates=False, gaps=[])

    everyone = await school_roster(db, viewer.tenant_id)
    registers = list(
        await db.scalars(
            select(AttendanceRegister).where(
                AttendanceRegister.tenant_id == viewer.tenant_id,
                AttendanceRegister.date.in_(days),
            )
        )
    )
    by_lesson = {(r.lesson_id, r.date): r for r in registers if r.lesson_id}
    daily_by = {(r.class_name, r.date): r for r in registers if r.kind == "daily"}
    daily_classes = await _daily_classes_by_term(db, viewer, viewer.terms)
    creators = await user_names(db, (r.created_by for r in registers if r.lesson_id is None))
    staff_names = {
        s.id: staff_name(s)
        for s in await db.scalars(select(Staff).where(Staff.tenant_id == viewer.tenant_id))
    }

    found: list[GapRead] = []

    def add(
        spec: Spec,
        register: AttendanceRegister | None,
        ids: set[uuid.UUID],
        teacher: PersonRead | None,
    ) -> None:
        marked = 0 if register is None else sum(1 for m in register.marks if m.student_id in ids)
        if ids and marked < len(ids):
            found.append(
                GapRead(
                    key=spec.key(),
                    date=spec.date,
                    kind=spec.kind,
                    class_name=spec.class_name,
                    stream=spec.stream,
                    subject_name=spec.subject_name,
                    starts_at=spec.starts_at,
                    teacher=teacher,
                    unmarked=len(ids) - marked,
                    total=len(ids),
                )
            )

    for day in days:
        timetable = await timetable_on(db, viewer, day)
        if timetable is not None:
            for lesson in timetable.lessons:
                if lesson.day != day.weekday():
                    continue
                spec = await lesson_spec(db, viewer, day, lesson, timetable)
                register = by_lesson.get((lesson.id, day))
                staff_id = register.staff_id if register and register.staff_id else lesson.staff_id
                if not viewer.approver and (staff_id is None or staff_id != viewer.staff_id):
                    continue
                teacher = (
                    PersonRead(id=staff_id, name=staff_names.get(staff_id)) if staff_id else None
                )
                add(
                    spec,
                    register,
                    everyone.ids(spec.class_name, spec.stream, spec.subject_id),
                    teacher,
                )

        for register in registers:
            if register.date != day or register.kind != "lesson" or register.lesson_id:
                continue
            if not viewer.approver and register.created_by != viewer.user_id:
                continue
            spec = Spec(
                kind="lesson",
                date=day,
                class_name=register.class_name,
                stream=register.stream,
                subject_id=register.subject_id,
                subject_name=register.subject_name,
                starts_at=register.starts_at,
            )
            teacher = (
                PersonRead(id=register.created_by, name=creators.get(register.created_by))
                if register.created_by
                else None
            )
            add(
                spec,
                register,
                everyone.ids(register.class_name, register.stream, register.subject_id),
                teacher,
            )

        # Daily registers have no named teacher, so only approvers see their gaps.
        term = term_on(day, viewer.terms)
        if viewer.approver and term is not None:
            for name in sorted(daily_classes.get((term.label, term.year), set())):
                spec = Spec(kind="daily", date=day, class_name=name, stream=None)
                add(spec, daily_by.get((name, day)), everyone.ids(name, None, None), None)

    found.sort(key=lambda g: (g.date, g.starts_at or time.min), reverse=True)
    return GapsRead(start=start, end=end, needs_term_dates=False, gaps=found)
