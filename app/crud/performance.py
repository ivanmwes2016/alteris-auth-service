"""
Service layer for academic performance (marks entry).

Marks are stored one row per (student, subject) for a given class/term/year,
and belong to the student by id, so they follow a student through a rename or a
class change. Older marks that couldn't be matched to a student keep
student_id NULL and are identified by name instead.
A PATCH replaces the marks of the students it contains and leaves everyone
else alone, so a paged screen can save just the students it edited. For a
student in the payload, a blank/missing score for a subject means "not
persisted": any existing row for it gets deleted rather than stored as an
empty string.

`classAverage` and trend averages only count entered numeric scores — a
student with no mark yet for a subject isn't counted as a zero.
"""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.academics import SchoolClass, Subject, SubjectClass
from app.db.models.performance import PerformanceRecord
from app.db.models.student import Student
from app.db.schemas.performance import (
    STUDENT_KEYS,
    PerformancePage,
    PerformanceRead,
    PerformanceSummary,
    PerformanceUpdate,
    RankedMarks,
    TopStudent,
    TrendPoint,
)
from app.helpers.pagination import PageParams


def _safe_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


async def _get_records(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_name: str,
    term_label: str,
    year: int,
) -> list[PerformanceRecord]:
    result = await db.execute(
        select(PerformanceRecord).where(
            PerformanceRecord.tenant_id == tenant_id,
            PerformanceRecord.class_name == class_name,
            PerformanceRecord.term_label == term_label,
            PerformanceRecord.year == year,
        )
    )
    return list(result.scalars().all())


async def _get_trends(
    db: AsyncSession, *, tenant_id: uuid.UUID, class_name: str
) -> list[TrendPoint]:
    result = await db.execute(
        select(PerformanceRecord).where(
            PerformanceRecord.tenant_id == tenant_id,
            PerformanceRecord.class_name == class_name,
        )
    )
    records = result.scalars().all()

    by_term: dict[tuple[int, str], list[float]] = defaultdict(list)
    for record in records:
        score = _safe_float(record.score)
        if score is not None:
            by_term[(record.year, record.term_label)].append(score)

    points: list[TrendPoint] = []
    for year, term_label in sorted(by_term.keys()):
        scores = by_term[(year, term_label)]
        points.append(
            TrendPoint(term=f"{term_label} {year}", average=round(sum(scores) / len(scores), 1))
        )
    return points


class UnknownStudentError(ValueError):
    """A marks row named a student_id that isn't a student of this school."""


# A row's identity: the student's id, or ("name", name) for unmatched marks.
RowKey = uuid.UUID | tuple[str, str]


def _record_key(record: PerformanceRecord) -> RowKey:
    return record.student_id if record.student_id else ("name", record.student_name)


async def _students_by_id(
    db: AsyncSession, tenant_id: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, Student]:
    """This school's students with these ids (others are left out)."""
    if not ids:
        return {}
    result = await db.scalars(
        select(Student).where(Student.tenant_id == tenant_id, Student.id.in_(ids))
    )
    return {s.id: s for s in result.all()}


def _marks_row(key: RowKey, name: str, school_id: str | None) -> dict[str, str]:
    row = {"student": name}
    if isinstance(key, uuid.UUID):
        row["student_id"] = str(key)
        if school_id:
            row["school_id"] = school_id
    return row


async def get_performance(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_name: str,
    term_label: str,
    year: int,
) -> PerformanceRead:
    records = await _get_records(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )
    # Show students by their current name, not the one saved with the mark.
    students = await _students_by_id(db, tenant_id, {r.student_id for r in records if r.student_id})

    marks_by_student: dict[RowKey, dict[str, str]] = {}
    scores_by_subject: dict[str, list[float]] = defaultdict(list)
    # Insertion-ordered, so leftover subjects keep the order their marks came in.
    subjects_seen: dict[str, None] = {}

    for record in records:
        key = _record_key(record)
        student = students.get(record.student_id) if record.student_id else None
        row = marks_by_student.get(key)
        if row is None:
            row = marks_by_student[key] = _marks_row(
                key,
                student.name if student else record.student_name,
                student.school_id if student else None,
            )
        row[record.subject] = record.score
        subjects_seen.setdefault(record.subject)

        score = _safe_float(record.score)
        if score is not None:
            scores_by_subject[record.subject].append(score)

    class_average = {
        subject: round(sum(scores) / len(scores), 1)
        for subject, scores in scores_by_subject.items()
        if scores
    }

    trends = await _get_trends(db, tenant_id=tenant_id, class_name=class_name)

    # The class's subjects in the order they were added, not alphabetical;
    # any other subject that has marks follows.
    class_subjects = await _class_subjects(db, tenant_id, class_name)
    subjects = class_subjects + [s for s in subjects_seen if s not in class_subjects]

    return PerformanceRead(
        subjects=subjects,
        marks=list(marks_by_student.values()),
        classAverage=class_average,
        trends=trends,
    )


def _row_key(row: dict[str, str]) -> RowKey | None:
    """Which student a payload row is for: their id, else (older marks) their name."""
    raw_id = (row.get("student_id") or "").strip()
    if raw_id:
        try:
            return uuid.UUID(raw_id)
        except ValueError as exc:
            raise UnknownStudentError(f"Unknown student: {raw_id}") from exc
    name = (row.get("student") or "").strip()
    return ("name", name) if name else None


async def sync_marks(
    db: AsyncSession, *, tenant_id: uuid.UUID, data: PerformanceUpdate
) -> PerformanceRead:
    class_name = data.class_name
    term_label = data.term
    year = data.year

    rows = [(rk, row) for row in data.marks if (rk := _row_key(row)) is not None]
    ids = {rk for rk, _ in rows if isinstance(rk, uuid.UUID)}
    students = await _students_by_id(db, tenant_id, ids)
    # Only this school's students can be given marks.
    if unknown := ids - students.keys():
        raise UnknownStudentError(f"Unknown student: {sorted(map(str, unknown))[0]}")

    existing = await _get_records(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )
    existing_by_key = {(_record_key(r), r.subject): r for r in existing}
    seen_keys: set[tuple[RowKey, str]] = set()
    # Only these students' marks are replaced; everyone else keeps theirs.
    students_sent = {rk for rk, _ in rows}

    for student_key, row in rows:
        if isinstance(student_key, uuid.UUID):
            student_id: uuid.UUID | None = student_key
            student_name = students[student_key].name
        else:
            student_id, student_name = None, student_key[1]

        for subject, score in row.items():
            if subject in STUDENT_KEYS:
                continue

            key = (student_key, subject)

            if not score or not score.strip():
                continue  # blank entry -> not persisted; any existing row is removed below

            existing_row = existing_by_key.get(key)
            if existing_row is not None:
                existing_row.score = score
                existing_row.student_name = student_name
            else:
                db.add(
                    PerformanceRecord(
                        tenant_id=tenant_id,
                        class_name=class_name,
                        term_label=term_label,
                        year=year,
                        student_id=student_id,
                        student_name=student_name,
                        subject=subject,
                        score=score,
                    )
                )
            seen_keys.add(key)

    for key, existing_row in existing_by_key.items():
        if key[0] in students_sent and key not in seen_keys:
            await db.delete(existing_row)

    await db.commit()

    return await get_performance(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )


# ---------------------------------------------------------------------------
# Paged view: roster, ranking and class-wide figures computed here, so the
# page only downloads the rows it shows.
# ---------------------------------------------------------------------------
@dataclass
class _RosterEntry:
    """A student on the ranked roster, before ranking."""

    student_id: uuid.UUID | None
    name: str
    school_id: str | None
    scores: dict[str, str] = field(default_factory=dict)


def _round_half_up(value: float) -> int:
    """Same rounding as the frontend's Math.round (Python's round() is banker's)."""
    return math.floor(value + 0.5)


def _student_average(scores: dict[str, str], subjects: list[str]) -> float:
    """Mean over the class's subjects; a missing or non-numeric mark counts as 0."""
    if not subjects:
        return 0.0
    return sum(_safe_float(scores.get(s, "") or "") or 0.0 for s in subjects) / len(subjects)


def _display_name(cls: SchoolClass) -> str:
    # Mirrors the frontend's classDisplayName: name and stream joined by a space.
    return " ".join(part for part in (cls.name, cls.stream) if part)


async def _class_roster(db: AsyncSession, tenant_id: uuid.UUID, class_name: str) -> list[Student]:
    """The students enrolled in the class, matched as the Students page does."""
    classes = (
        await db.scalars(select(SchoolClass).where(SchoolClass.tenant_id == tenant_id))
    ).all()
    match = next((c for c in classes if _display_name(c) == class_name), None)
    if match is None:
        return []

    query = select(Student).where(
        Student.tenant_id == tenant_id, Student.class_applied == match.name
    )
    query = query.where(
        Student.faculty == match.stream if match.stream else Student.faculty.is_(None)
    )
    return list((await db.scalars(query)).all())


async def _class_subjects(db: AsyncSession, tenant_id: uuid.UUID, class_name: str) -> list[str]:
    """Subjects assigned to the class, in the order they were added."""
    result = await db.scalars(
        select(Subject.name)
        .join(SubjectClass, SubjectClass.subject_id == Subject.id)
        .where(Subject.tenant_id == tenant_id, SubjectClass.name == class_name)
        .order_by(Subject.created_at, Subject.id)
    )
    return list(dict.fromkeys(result.all()))


async def get_performance_page(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_name: str,
    term_label: str,
    year: int,
    params: PageParams,
) -> PerformancePage:
    assert params.page is not None
    base = await get_performance(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )
    subjects = await _class_subjects(db, tenant_id, class_name) or base.subjects

    # Every enrolled student gets a row, plus anyone with marks who isn't on
    # the roster any more (e.g. they moved class), so no saved mark is hidden.
    entries: dict[RowKey, _RosterEntry] = {
        s.id: _RosterEntry(s.id, s.name, s.school_id)
        for s in await _class_roster(db, tenant_id, class_name)
    }
    for row in base.marks:
        raw_id = row.get("student_id")
        key: RowKey = uuid.UUID(raw_id) if raw_id else ("name", row["student"])
        entry = entries.get(key)
        if entry is None:
            entry = entries[key] = _RosterEntry(
                key if isinstance(key, uuid.UUID) else None,
                row["student"],
                row.get("school_id"),
            )
        entry.scores = {k: v for k, v in row.items() if k not in STUDENT_KEYS}

    averages = {key: _student_average(e.scores, subjects) for key, e in entries.items()}
    ranked = sorted(entries, key=lambda k: (-averages[k], entries[k].name.lower()))
    rows = [
        RankedMarks(
            student_id=entries[key].student_id,
            school_id=entries[key].school_id,
            student=entries[key].name,
            rank=position,
            average=_round_half_up(averages[key]),
            scores=entries[key].scores,
        )
        for position, key in enumerate(ranked, start=1)
    ]

    summary = PerformanceSummary(
        students=len(rows),
        average=_round_half_up(sum(r.average for r in rows) / len(rows)) if rows else 0,
        top=(
            TopStudent(
                student_id=rows[0].student_id, student=rows[0].student, average=rows[0].average
            )
            if rows
            else None
        ),
    )

    # Search (name or school id) narrows the rows shown; ranks stay the
    # student's rank in the class.
    if params.search:
        term = params.search.lower()
        rows = [r for r in rows if term in r.student.lower() or term in (r.school_id or "").lower()]

    start = (params.page - 1) * params.page_size
    return PerformancePage(
        items=rows[start : start + params.page_size],
        total=len(rows),
        page=params.page,
        page_size=params.page_size,
        pages=max(1, math.ceil(len(rows) / params.page_size)),
        subjects=subjects,
        classAverage=base.classAverage,
        trends=base.trends,
        summary=summary,
    )
