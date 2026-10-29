"""
Service layer for the Timetable page.

Anyone in the school can read timetables. Changing one needs the Manage
Timetable roles (Owner, Head Teacher, Admin). A class teacher (a staff record
with the Class Teacher rank, linked to the account) may also add, change,
swap and remove their own class's lessons, but nothing else. Past terms can't
be changed at all: a term is past when it comes before the school's current
term, by year and then by its place in the school's term labels.

Clashes aren't checked here. They are allowed (the page flags them), so the
API only refuses lessons that can't exist: outside the school days, starting
in a break, or running past a break or the end of the day.

Every query is scoped to the caller's tenant; ids from other tenants 404.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import team
from app.crud.academic_term import get_settings
from app.crud.attendance import linked_staff_id
from app.db.models.academics import SchoolClass, Subject
from app.db.models.staff import Staff
from app.db.models.timetable import Lesson, OptionsPeriod, Timetable, TimetablePeriod
from app.db.models.users import User
from app.db.schemas.timetable import (
    BellScheduleImpact,
    BellScheduleWrite,
    LessonCreate,
    LessonRead,
    LessonSwap,
    LessonUpdate,
    OptionsPeriodCreate,
    OptionsPeriodRead,
    TimetableCreate,
    TimetableRead,
    TimetableSummary,
)

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
MANAGE_TIMETABLE_ROLES = {"head_teacher", "admin"}
CLASS_TEACHER_RANK = "Class Teacher"


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
class PeriodLike(Protocol):
    @property
    def id(self) -> uuid.UUID | None: ...

    @property
    def kind(self) -> str: ...


@dataclass(frozen=True)
class Slot:
    id: uuid.UUID | None
    kind: str


@dataclass(frozen=True)
class Placement:
    id: uuid.UUID
    day: int
    period_id: uuid.UUID
    length: int


def can_manage_timetable(*, is_owner: bool, role_name: str) -> bool:
    """Matches the Users & Roles matrix: Owner, Head Teacher and Admin manage timetables."""
    return is_owner or role_name in MANAGE_TIMETABLE_ROLES


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def is_class_teacher_of(class_assigned: str | None, *, name: str, stream: str | None) -> bool:
    """Staff records name their class as free text ("Grade 9A", "grade 9 a"), so compare
    ignoring case, spaces and punctuation against the class's name plus its stream."""
    if not class_assigned or not _squash(class_assigned):
        return False
    return _squash(class_assigned) == _squash(name + (stream or ""))


def is_past_term(
    *,
    labels: Sequence[str],
    current_label: str | None,
    current_year: int | None,
    term: str,
    year: int,
) -> bool:
    """Before the current term. Without a current term (or unknown labels) nothing is past."""
    if current_year is None:
        return False
    if year != current_year:
        return year < current_year
    if current_label not in labels or term not in labels:
        return False
    return labels.index(term) < labels.index(current_label)


def teaching_run(periods: Sequence[PeriodLike], start_id: uuid.UUID) -> int:
    """How many back-to-back teaching periods start at `start_id` (0 if it's missing or a break)."""
    ids = [p.id for p in periods]
    if start_id not in ids:
        return 0
    run = 0
    for period in periods[ids.index(start_id) :]:
        if period.kind != "lesson":
            break
        run += 1
    return run


def plan_bell_schedule(
    new_periods: Sequence[PeriodLike],
    new_days: Sequence[int],
    lessons: Sequence[Placement],
) -> tuple[set[uuid.UUID], dict[uuid.UUID, int]]:
    """
    What a new bell schedule does to the lessons already placed: the ones
    removed (their day or first period is gone, or it became a break) and the
    ones shortened (fewer teaching periods now follow), with their new length.
    """
    removed: set[uuid.UUID] = set()
    shortened: dict[uuid.UUID, int] = {}
    for lesson in lessons:
        run = teaching_run(new_periods, lesson.period_id)
        if lesson.day not in new_days or run == 0:
            removed.add(lesson.id)
        elif run < lesson.length:
            shortened[lesson.id] = run
    return removed, shortened


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def _not_found(what: str = "Timetable") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def _term_taken() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail="This term already has a timetable"
    )


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


# ---------------------------------------------------------------------------
# Caller context
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Viewer:
    actor: team.Actor
    can_manage: bool
    labels: list[str]
    current_label: str | None
    current_year: int | None
    # The caller's staff record, and the classes they are class teacher of.
    staff_id: uuid.UUID | None = None
    own_class_ids: frozenset[uuid.UUID] = frozenset()
    # Every class in the school; loaded for managers only, who may edit them all.
    all_class_ids: frozenset[uuid.UUID] = frozenset()

    def is_past(self, timetable: Timetable) -> bool:
        return is_past_term(
            labels=self.labels,
            current_label=self.current_label,
            current_year=self.current_year,
            term=timetable.term,
            year=timetable.year,
        )

    def can_edit(self, timetable: Timetable) -> bool:
        """The whole timetable: bell schedule, options periods and every class's lessons."""
        return self.can_manage and not self.is_past(timetable)

    def lesson_class_ids(self, timetable: Timetable) -> frozenset[uuid.UUID]:
        """The classes whose lessons the caller may change on this timetable."""
        if self.is_past(timetable):
            return frozenset()
        return self.all_class_ids if self.can_manage else self.own_class_ids


async def get_viewer(db: AsyncSession, user: User) -> Viewer:
    actor = await team.get_actor(db, user)
    settings = await get_settings(db, tenant_id=actor.tenant_id)
    can_manage = can_manage_timetable(is_owner=actor.is_owner, role_name=actor.role_name)
    staff_id = await linked_staff_id(db, tenant_id=actor.tenant_id, user=user)
    staff = await db.get(Staff, staff_id) if staff_id else None
    classes = (
        await db.execute(
            select(SchoolClass.id, SchoolClass.name, SchoolClass.stream).where(
                SchoolClass.tenant_id == actor.tenant_id
            )
        )
    ).all()
    own: Iterable[uuid.UUID] = ()
    if staff is not None and staff.rank == CLASS_TEACHER_RANK:
        own = (
            c.id
            for c in classes
            if is_class_teacher_of(staff.class_assigned, name=c.name, stream=c.stream)
        )
    return Viewer(
        actor=actor,
        can_manage=can_manage,
        labels=list(settings.labels) if settings else [],
        current_label=settings.current_label if settings else None,
        current_year=settings.current_year if settings else None,
        staff_id=staff_id,
        own_class_ids=frozenset(own),
        all_class_ids=frozenset(c.id for c in classes) if can_manage else frozenset(),
    )


def _require_manager(viewer: Viewer) -> None:
    if not viewer.can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage the timetable",
        )


def _past_term() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Past terms' timetables can't be changed",
    )


def _require_editable(viewer: Viewer, timetable: Timetable) -> None:
    _require_manager(viewer)
    if not viewer.can_edit(timetable):
        raise _past_term()


def _require_class_editable(viewer: Viewer, timetable: Timetable, *class_ids: uuid.UUID) -> None:
    """Managers may change any class's lessons; class teachers only their own class's."""
    if viewer.can_manage:
        _require_editable(viewer, timetable)
        return
    if not viewer.own_class_ids:
        _require_manager(viewer)
    if not all(c in viewer.own_class_ids for c in class_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only change your own class's lessons",
        )
    if viewer.is_past(timetable):
        raise _past_term()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
async def _load(db: AsyncSession, tenant_id: uuid.UUID, timetable_id: uuid.UUID) -> Timetable:
    timetable = (
        await db.execute(
            select(Timetable)
            .where(Timetable.id == timetable_id, Timetable.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if timetable is None:
        raise _not_found()
    return timetable


def _read(viewer: Viewer, timetable: Timetable) -> TimetableRead:
    return TimetableRead(
        id=timetable.id,
        term=timetable.term,
        year=timetable.year,
        days=list(timetable.days),
        periods=timetable.periods,
        lessons=timetable.lessons,
        options_periods=timetable.options_periods,
        editable=viewer.can_edit(timetable),
        editable_class_ids=sorted(viewer.lesson_class_ids(timetable)),
        staff_id=viewer.staff_id,
    )


async def list_timetables(db: AsyncSession, viewer: Viewer) -> list[TimetableSummary]:
    rows = await db.execute(
        select(Timetable.id, Timetable.term, Timetable.year)
        .where(Timetable.tenant_id == viewer.actor.tenant_id)
        .order_by(Timetable.year.desc(), Timetable.term)
    )
    return [TimetableSummary(id=r.id, term=r.term, year=r.year) for r in rows]


async def get_timetable(db: AsyncSession, viewer: Viewer, *, term: str, year: int) -> TimetableRead:
    timetable = (
        await db.execute(
            select(Timetable).where(
                Timetable.tenant_id == viewer.actor.tenant_id,
                Timetable.term == term,
                Timetable.year == year,
            )
        )
    ).scalar_one_or_none()
    if timetable is None:
        raise _not_found()
    return _read(viewer, timetable)


# ---------------------------------------------------------------------------
# Timetables
# ---------------------------------------------------------------------------
async def create_timetable(
    db: AsyncSession, viewer: Viewer, payload: TimetableCreate
) -> TimetableRead:
    tenant_id = viewer.actor.tenant_id
    timetable = Timetable(tenant_id=tenant_id, term=payload.term, year=payload.year, days=ALL_DAYS)
    _require_editable(viewer, timetable)

    source = await _load(db, tenant_id, payload.copy_from) if payload.copy_from else None
    db.add(timetable)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _term_taken() from exc

    if source is not None:
        timetable.days = list(source.days)
        new_period_ids: dict[uuid.UUID, uuid.UUID] = {}
        for period in source.periods:
            copy = TimetablePeriod(
                tenant_id=tenant_id,
                timetable_id=timetable.id,
                position=period.position,
                starts_at=period.starts_at,
                ends_at=period.ends_at,
                kind=period.kind,
                label=period.label,
            )
            db.add(copy)
            await db.flush()
            new_period_ids[period.id] = copy.id
        db.add_all(
            Lesson(
                tenant_id=tenant_id,
                timetable_id=timetable.id,
                class_id=lesson.class_id,
                subject_id=lesson.subject_id,
                staff_id=lesson.staff_id,
                day=lesson.day,
                period_id=new_period_ids[lesson.period_id],
                length=lesson.length,
            )
            for lesson in source.lessons
        )
        db.add_all(
            OptionsPeriod(
                tenant_id=tenant_id,
                timetable_id=timetable.id,
                class_id=option.class_id,
                day=option.day,
                period_id=new_period_ids[option.period_id],
            )
            for option in source.options_periods
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _term_taken() from exc

    return _read(viewer, await _load(db, tenant_id, timetable.id))


# ---------------------------------------------------------------------------
# Bell schedule
# ---------------------------------------------------------------------------
def _placements(timetable: Timetable) -> list[Placement]:
    return [Placement(x.id, x.day, x.period_id, x.length) for x in timetable.lessons]


def _check_period_ids(timetable: Timetable, payload: BellScheduleWrite) -> None:
    existing = {p.id for p in timetable.periods}
    given = [p.id for p in payload.periods if p.id is not None]
    if len(given) != len(set(given)) or not set(given) <= existing:
        raise _invalid("Unknown or repeated period")


async def preview_bell_schedule(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, payload: BellScheduleWrite
) -> BellScheduleImpact:
    timetable = await _load(db, viewer.actor.tenant_id, timetable_id)
    _require_editable(viewer, timetable)
    _check_period_ids(timetable, payload)
    removed, shortened = plan_bell_schedule(payload.periods, payload.days, _placements(timetable))
    return BellScheduleImpact(removed_lessons=len(removed), shortened_lessons=len(shortened))


async def save_bell_schedule(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, payload: BellScheduleWrite
) -> TimetableRead:
    tenant_id = viewer.actor.tenant_id
    timetable = await _load(db, tenant_id, timetable_id)
    _require_editable(viewer, timetable)
    _check_period_ids(timetable, payload)

    removed, shortened = plan_bell_schedule(payload.periods, payload.days, _placements(timetable))
    for lesson in list(timetable.lessons):
        if lesson.id in removed:
            timetable.lessons.remove(lesson)
        elif lesson.id in shortened:
            lesson.length = shortened[lesson.id]

    kept_ids = {p.id for p in payload.periods if p.id is not None}
    teaching_ids = {p.id for p in payload.periods if p.id is not None and p.kind == "lesson"}
    for option in list(timetable.options_periods):
        if option.day not in payload.days or option.period_id not in teaching_ids:
            timetable.options_periods.remove(option)
    # Lessons and options periods go first: deleting a period cascades to its
    # rows in the database, which the ORM would then try to delete again.
    await db.flush()
    for old in list(timetable.periods):
        if old.id not in kept_ids:
            timetable.periods.remove(old)

    by_id = {p.id: p for p in timetable.periods}
    for position, written in enumerate(payload.periods):
        period = by_id.get(written.id) if written.id else None
        if period is None:
            period = TimetablePeriod(tenant_id=tenant_id, timetable_id=timetable.id)
            timetable.periods.append(period)
        period.position = position
        period.starts_at = written.starts_at
        period.ends_at = written.ends_at
        period.kind = written.kind
        period.label = written.label

    timetable.days = payload.days
    await db.commit()
    return _read(viewer, await _load(db, tenant_id, timetable_id))


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------
async def _check_refs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    staff_id: uuid.UUID | None = None,
) -> None:
    for model, ref_id, what in (
        (SchoolClass, class_id, "Class"),
        (Subject, subject_id, "Subject"),
        (Staff, staff_id, "Staff member"),
    ):
        if ref_id is None:
            continue
        found = await db.scalar(
            select(model.id).where(model.id == ref_id, model.tenant_id == tenant_id)
        )
        if found is None:
            raise _not_found(what)


def _check_place(timetable: Timetable, *, day: int, period_id: uuid.UUID, length: int) -> None:
    if day not in timetable.days:
        raise _invalid("That day isn't a school day on this timetable")
    run = teaching_run([Slot(p.id, p.kind) for p in timetable.periods], period_id)
    if run == 0:
        raise _invalid("Lessons go in teaching periods, not breaks")
    if run < length:
        raise _invalid("The lesson would run into a break or past the end of the day")


def _find_lesson(timetable: Timetable, lesson_id: uuid.UUID) -> Lesson:
    lesson = next((x for x in timetable.lessons if x.id == lesson_id), None)
    if lesson is None:
        raise _not_found("Lesson")
    return lesson


async def create_lesson(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, payload: LessonCreate
) -> LessonRead:
    tenant_id = viewer.actor.tenant_id
    timetable = await _load(db, tenant_id, timetable_id)
    _require_class_editable(viewer, timetable, payload.class_id)
    _check_place(timetable, day=payload.day, period_id=payload.period_id, length=payload.length)
    await _check_refs(
        db,
        tenant_id,
        class_id=payload.class_id,
        subject_id=payload.subject_id,
        staff_id=payload.staff_id,
    )

    lesson = Lesson(tenant_id=tenant_id, timetable_id=timetable.id, **payload.model_dump())
    db.add(lesson)
    await db.commit()
    return LessonRead.model_validate(lesson)


async def update_lesson(
    db: AsyncSession,
    viewer: Viewer,
    timetable_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonUpdate,
) -> LessonRead:
    tenant_id = viewer.actor.tenant_id
    timetable = await _load(db, tenant_id, timetable_id)
    lesson = _find_lesson(timetable, lesson_id)
    _require_class_editable(viewer, timetable, lesson.class_id)

    # Only the teacher can be cleared; any other field sent as null is left as it is.
    changes = {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if value is not None or field == "staff_id"
    }
    _check_place(
        timetable,
        day=payload.day if payload.day is not None else lesson.day,
        period_id=payload.period_id or lesson.period_id,
        length=payload.length or lesson.length,
    )
    await _check_refs(db, tenant_id, subject_id=payload.subject_id, staff_id=payload.staff_id)

    for field, value in changes.items():
        setattr(lesson, field, value)
    await db.commit()
    return LessonRead.model_validate(lesson)


async def swap_lessons(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, payload: LessonSwap
) -> list[LessonRead]:
    timetable = await _load(db, viewer.actor.tenant_id, timetable_id)
    first = _find_lesson(timetable, payload.first_id)
    second = _find_lesson(timetable, payload.second_id)
    _require_class_editable(viewer, timetable, first.class_id, second.class_id)

    _check_place(timetable, day=second.day, period_id=second.period_id, length=first.length)
    _check_place(timetable, day=first.day, period_id=first.period_id, length=second.length)
    first.day, second.day = second.day, first.day
    first.period_id, second.period_id = second.period_id, first.period_id
    await db.commit()
    return [LessonRead.model_validate(first), LessonRead.model_validate(second)]


async def delete_lesson(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, lesson_id: uuid.UUID
) -> None:
    timetable = await _load(db, viewer.actor.tenant_id, timetable_id)
    lesson = _find_lesson(timetable, lesson_id)
    _require_class_editable(viewer, timetable, lesson.class_id)
    timetable.lessons.remove(lesson)
    await db.commit()


# ---------------------------------------------------------------------------
# Options periods
# ---------------------------------------------------------------------------
async def create_options_period(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, payload: OptionsPeriodCreate
) -> OptionsPeriodRead:
    tenant_id = viewer.actor.tenant_id
    timetable = await _load(db, tenant_id, timetable_id)
    _require_editable(viewer, timetable)
    _check_place(timetable, day=payload.day, period_id=payload.period_id, length=1)
    await _check_refs(db, tenant_id, class_id=payload.class_id)

    option = OptionsPeriod(tenant_id=tenant_id, timetable_id=timetable.id, **payload.model_dump())
    db.add(option)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That period is already an options period"
        ) from exc
    return OptionsPeriodRead.model_validate(option)


async def delete_options_period(
    db: AsyncSession, viewer: Viewer, timetable_id: uuid.UUID, option_id: uuid.UUID
) -> None:
    timetable = await _load(db, viewer.actor.tenant_id, timetable_id)
    _require_editable(viewer, timetable)
    option = next((o for o in timetable.options_periods if o.id == option_id), None)
    if option is None:
        raise _not_found("Options period")
    timetable.options_periods.remove(option)
    await db.commit()
