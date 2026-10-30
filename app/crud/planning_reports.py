"""
Lesson planning's week view and reports: unplanned, unrecorded and late
lessons, records that disagree with the register, each stream's progress
through its scheme, and the record of work.

Gaps count the same days as register gaps (finished term days that weren't
no-school days) and land on the same person: a timetabled lesson's teacher,
or whoever added an extra lesson. Teachers see their own; approvers see
everyone's, and alone see records that disagree with the register.
"""

import uuid
from collections.abc import Sequence
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.attendance import (
    Viewer,
    counts_for_gaps,
    lesson_spec,
    term_on,
    timetable_on,
    user_names,
)
from app.crud.attendance_reports import check_range
from app.crud.planning import (
    LessonSpec,
    _plan_summary,
    _scheme,
    _staff_names,
    lesson_minutes,
    planned_key,
    progress_for,
    record_read,
    term_span,
    term_week,
    week_monday,
)
from app.db.models.attendance import AttendanceRegister, NoSchoolDay
from app.db.models.planning import LessonPlan, PlannedLesson, SchemeOfWork
from app.db.schemas.attendance import PersonRead
from app.db.schemas.planning import (
    LessonKey,
    PlanningGap,
    PlanningGapsRead,
    PlanSummary,
    RecordOfWorkRead,
    RecordOfWorkRow,
    SchemeProgressRead,
    WeekDay,
    WeekLesson,
    WeekRead,
)


async def _timetabled(db: AsyncSession, viewer: Viewer, day: date) -> list[LessonSpec]:
    """The day's timetabled lessons, in period order."""
    timetable = await timetable_on(db, viewer, day)
    if timetable is None:
        return []
    starts = {p.id: (p.position, p.starts_at) for p in timetable.periods}
    found = []
    for lesson in sorted(
        (lesson for lesson in timetable.lessons if lesson.day == day.weekday()),
        key=lambda lesson: starts.get(lesson.period_id, (0, time.min)),
    ):
        spec = await lesson_spec(db, viewer, day, lesson, timetable)
        if spec.starts_at is None:
            continue
        found.append(
            LessonSpec(
                date=day,
                lesson_id=lesson.id,
                class_name=spec.class_name,
                stream=spec.stream,
                subject_id=spec.subject_id,
                subject_name=spec.subject_name or "",
                starts_at=spec.starts_at,
                ends_at=spec.ends_at,
                minutes=lesson_minutes(spec.starts_at, spec.ends_at) or 0,
                staff_id=spec.staff_id,
                teacher_name=spec.teacher_name,
            )
        )
    return found


async def _rows_between(
    db: AsyncSession, viewer: Viewer, start: date, end: date
) -> list[PlannedLesson]:
    return list(
        await db.scalars(
            select(PlannedLesson).where(
                PlannedLesson.tenant_id == viewer.tenant_id, PlannedLesson.date.between(start, end)
            )
        )
    )


async def _plans(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, LessonPlan]:
    if not ids:
        return {}
    return {p.id: p for p in await db.scalars(select(LessonPlan).where(LessonPlan.id.in_(ids)))}


async def get_week(
    db: AsyncSession, viewer: Viewer, day: date | None, *, mine_only: bool
) -> WeekRead:
    """The week (Monday to Sunday) holding `day`: every lesson, its plan and its record."""
    start = week_monday(day or viewer.today)
    end = start + timedelta(days=6)
    rows = await _rows_between(db, viewer, start, end)
    by_lesson: dict[tuple[uuid.UUID | None, date], PlannedLesson] = {
        (r.lesson_id, r.date): r for r in rows if r.lesson_id
    }
    plans = await _plans(db, {r.plan_id for r in rows if r.plan_id})
    staff = await _staff_names(db, viewer)
    names = await user_names(db, (r.recorded_by for r in rows))
    summaries: dict[uuid.UUID, PlanSummary] = {
        p.id: await _plan_summary(db, viewer, p, staff) for p in plans.values()
    }
    closed = {
        d.date: d.reason or "No school"
        for d in await db.scalars(
            select(NoSchoolDay).where(
                NoSchoolDay.tenant_id == viewer.tenant_id, NoSchoolDay.date.between(start, end)
            )
        )
    }

    def lesson(
        spec: LessonSpec, row: PlannedLesson | None, *, mine: bool, extra: bool
    ) -> WeekLesson:
        teacher_id = spec.staff_id
        return WeekLesson(
            key=spec.key(),
            class_name=spec.class_name,
            stream=spec.stream,
            subject_id=spec.subject_id,
            subject_name=spec.subject_name,
            starts_at=spec.starts_at,
            ends_at=spec.ends_at,
            minutes=spec.minutes,
            teacher=PersonRead(id=teacher_id, name=staff.get(teacher_id)) if teacher_id else None,
            mine=mine,
            extra=extra,
            plan=summaries.get(row.plan_id) if row and row.plan_id else None,
            record=record_read(row, names) if row else None,
        )

    days = []
    for n in range(7):
        current = start + timedelta(days=n)
        lessons = []
        for spec in await _timetabled(db, viewer, current):
            mine = viewer.staff_id is not None and spec.staff_id == viewer.staff_id
            if mine_only and not mine:
                continue
            lessons.append(
                lesson(spec, by_lesson.get((spec.lesson_id, current)), mine=mine, extra=False)
            )
        for row in rows:
            if row.date != current or row.lesson_id is not None:
                continue
            mine = row.created_by == viewer.user_id
            if mine_only and not mine:
                continue
            spec = LessonSpec(
                date=row.date,
                lesson_id=None,
                class_name=row.class_name,
                stream=row.stream,
                subject_id=row.subject_id,
                subject_name=row.subject_name,
                starts_at=row.starts_at,
                ends_at=None,
                minutes=row.minutes,
                staff_id=row.staff_id,
                teacher_name=None,
            )
            lessons.append(lesson(spec, row, mine=mine, extra=True))
        lessons.sort(key=lambda item: item.starts_at)
        days.append(WeekDay(date=current, no_school=closed.get(current), lessons=lessons))

    term = term_on(start, viewer.terms) or term_on(end, viewer.terms)
    return WeekRead(
        starts_on=start,
        today=viewer.today,
        term=term.label if term else None,
        year=term.year if term else None,
        week=term_week(max(start, term.starts_on), term) if term else None,
        days=days,
    )


async def gaps(db: AsyncSession, viewer: Viewer, *, start: date, end: date) -> PlanningGapsRead:
    check_range(start, end)
    if not viewer.terms:
        return PlanningGapsRead(start=start, end=end, needs_term_dates=True, gaps=[])
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
    rows = await _rows_between(db, viewer, start, end)
    by_lesson: dict[tuple[uuid.UUID | None, date], PlannedLesson] = {
        (r.lesson_id, r.date): r for r in rows if r.lesson_id
    }
    plans = await _plans(db, {r.plan_id for r in rows if r.plan_id})
    staff = await _staff_names(db, viewer)
    creators = await user_names(db, (r.created_by for r in rows))
    registers = list(
        await db.scalars(
            select(AttendanceRegister).where(
                AttendanceRegister.tenant_id == viewer.tenant_id,
                AttendanceRegister.kind == "lesson",
                AttendanceRegister.date.in_(days),
            )
        )
    )
    taken_lessons = {(r.lesson_id, r.date) for r in registers if r.lesson_id and r.marks}
    taken_extra = {
        (r.class_name, r.stream, r.subject_id, r.date, r.starts_at)
        for r in registers
        if not r.lesson_id and r.marks
    }

    found: list[PlanningGap] = []

    def add(
        kind: str,
        key: LessonKey,
        row_or_spec: PlannedLesson | LessonSpec,
        teacher: PersonRead | None,
        plan_id: uuid.UUID | None,
    ) -> None:
        found.append(
            PlanningGap(
                kind=kind,
                key=key,
                date=row_or_spec.date,
                class_name=row_or_spec.class_name,
                stream=row_or_spec.stream,
                subject_name=row_or_spec.subject_name,
                starts_at=row_or_spec.starts_at,
                teacher=teacher,
                plan_id=plan_id,
            )
        )

    def judge(
        row: PlannedLesson | None, spec: LessonSpec, teacher: PersonRead | None, taken: bool
    ) -> None:
        planned = row is not None and row.plan_id is not None
        recorded = row is not None and row.record_status is not None
        if not planned and not (recorded and row is not None and row.record_status == "not_taught"):
            add("unplanned", spec.key(), spec, teacher, None)
            return
        if row is None:
            return
        plan = plans.get(row.plan_id) if row.plan_id else None
        if plan is not None and plan.late:
            add("late", spec.key(), spec, teacher, plan.id)
        if planned and not recorded:
            add("unrecorded", spec.key(), spec, teacher, row.plan_id)
        if viewer.approver:
            if row.record_status in ("taught", "partly_taught") and not taken:
                add("taught_no_register", spec.key(), spec, teacher, row.plan_id)
            if row.record_status == "not_taught" and taken:
                add("register_not_taught", spec.key(), spec, teacher, row.plan_id)

    for day in days:
        for spec in await _timetabled(db, viewer, day):
            if not viewer.approver and (spec.staff_id is None or spec.staff_id != viewer.staff_id):
                continue
            teacher = (
                PersonRead(id=spec.staff_id, name=staff.get(spec.staff_id))
                if spec.staff_id
                else None
            )
            judge(
                by_lesson.get((spec.lesson_id, day)),
                spec,
                teacher,
                (spec.lesson_id, day) in taken_lessons,
            )
        for row in rows:
            if row.date != day or row.lesson_id is not None:
                continue
            if not viewer.approver and row.created_by != viewer.user_id:
                continue
            spec = LessonSpec(
                date=row.date,
                lesson_id=None,
                class_name=row.class_name,
                stream=row.stream,
                subject_id=row.subject_id,
                subject_name=row.subject_name,
                starts_at=row.starts_at,
                ends_at=None,
                minutes=row.minutes,
                staff_id=row.staff_id,
                teacher_name=None,
            )
            teacher = (
                PersonRead(id=row.created_by, name=creators.get(row.created_by))
                if row.created_by
                else None
            )
            taken = (
                row.class_name,
                row.stream,
                row.subject_id,
                row.date,
                row.starts_at,
            ) in taken_extra
            judge(row, spec, teacher, taken)

    found.sort(key=lambda g: (g.date, g.starts_at), reverse=True)
    return PlanningGapsRead(start=start, end=end, needs_term_dates=False, gaps=found)


async def progress(
    db: AsyncSession, viewer: Viewer, *, term: str, year: int
) -> list[SchemeProgressRead]:
    """Every scheme of the term, each stream's place in it."""
    span = term_span(viewer, term, year)
    current = (
        term_week(viewer.today, span)
        if span is not None and span.starts_on <= viewer.today <= span.ends_on
        else None
    )
    schemes = list(
        await db.scalars(
            select(SchemeOfWork)
            .where(
                SchemeOfWork.tenant_id == viewer.tenant_id,
                SchemeOfWork.term == term,
                SchemeOfWork.year == year,
            )
            .order_by(SchemeOfWork.class_name, SchemeOfWork.subject_name)
        )
    )
    return [
        SchemeProgressRead(
            scheme_id=s.id,
            class_name=s.class_name,
            subject_name=s.subject_name,
            status=s.status,
            current_week=current,
            streams=await progress_for(db, viewer, s, current),
        )
        for s in schemes
    ]


async def record_of_work(
    db: AsyncSession, viewer: Viewer, scheme_id: uuid.UUID
) -> RecordOfWorkRead:
    """
    Every lesson planned or recorded for a scheme's class and subject that term,
    with what it covered: the page a head teacher prints for an inspector.
    """
    scheme = await _scheme(db, viewer, scheme_id)
    span = term_span(viewer, scheme.term, scheme.year)
    query = (
        select(PlannedLesson)
        .outerjoin(LessonPlan, LessonPlan.id == PlannedLesson.plan_id)
        .where(
            PlannedLesson.tenant_id == viewer.tenant_id,
            PlannedLesson.class_name == scheme.class_name,
            PlannedLesson.subject_id == scheme.subject_id,
        )
        .order_by(PlannedLesson.date, PlannedLesson.starts_at, PlannedLesson.stream)
    )
    if span is not None:
        query = query.where(PlannedLesson.date.between(span.starts_on, span.ends_on))
    else:
        query = query.where(LessonPlan.term == scheme.term, LessonPlan.year == scheme.year)
    rows: Sequence[PlannedLesson] = list(await db.scalars(query))
    plans = await _plans(db, {r.plan_id for r in rows if r.plan_id})
    staff = await _staff_names(db, viewer)
    creators = await user_names(db, (r.created_by for r in rows if r.lesson_id is None))

    def teacher(row: PlannedLesson) -> PersonRead | None:
        if row.lesson_id is not None and row.staff_id:
            return PersonRead(id=row.staff_id, name=staff.get(row.staff_id))
        if row.created_by:
            return PersonRead(id=row.created_by, name=creators.get(row.created_by))
        return None

    return RecordOfWorkRead(
        class_name=scheme.class_name,
        subject_name=scheme.subject_name,
        term=scheme.term,
        year=scheme.year,
        rows=[
            RecordOfWorkRow(
                date=r.date,
                week=term_week(r.date, span) if span else None,
                stream=r.stream,
                starts_at=r.starts_at,
                topic=plans[r.plan_id].topic if r.plan_id in plans else None,
                subtopic=plans[r.plan_id].subtopic if r.plan_id in plans else None,
                status=r.record_status,
                reason=r.not_taught_reason,
                comment=r.record_comment,
                teacher=teacher(r),
            )
            for r in rows
        ],
    )


__all__ = ["gaps", "get_week", "planned_key", "progress", "record_of_work"]
