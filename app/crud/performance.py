"""
Service layer for academic performance (marks entry).

Marks are stored one row per (student, subject) for a given class/term/year.
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.academics import SchoolClass, Subject, SubjectClass
from app.db.models.performance import PerformanceRecord
from app.db.models.student import Student
from app.db.schemas.performance import (
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

    marks_by_student: dict[str, dict[str, str]] = {}
    scores_by_subject: dict[str, list[float]] = defaultdict(list)
    # Insertion-ordered, so leftover subjects keep the order their marks came in.
    subjects_seen: dict[str, None] = {}

    for record in records:
        row = marks_by_student.setdefault(record.student_name, {"student": record.student_name})
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


async def sync_marks(
    db: AsyncSession, *, tenant_id: uuid.UUID, data: PerformanceUpdate
) -> PerformanceRead:
    class_name = data.class_name
    term_label = data.term
    year = data.year

    existing = await _get_records(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )
    existing_by_key = {(r.student_name, r.subject): r for r in existing}
    seen_keys: set[tuple[str, str]] = set()
    # Only these students' marks are replaced; everyone else keeps theirs.
    students_sent = {row["student"] for row in data.marks if row.get("student")}

    for row in data.marks:
        student_name = row.get("student")
        if not student_name:
            continue

        for subject, score in row.items():
            if subject == "student":
                continue

            key = (student_name, subject)

            if not score or not score.strip():
                continue  # blank entry -> not persisted; any existing row is removed below

            existing_row = existing_by_key.get(key)
            if existing_row is not None:
                existing_row.score = score
            else:
                db.add(
                    PerformanceRecord(
                        tenant_id=tenant_id,
                        class_name=class_name,
                        term_label=term_label,
                        year=year,
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


async def _class_roster(db: AsyncSession, tenant_id: uuid.UUID, class_name: str) -> list[str]:
    """Names of the students enrolled in the class, matched as the Students page does."""
    classes = (
        await db.scalars(select(SchoolClass).where(SchoolClass.tenant_id == tenant_id))
    ).all()
    match = next((c for c in classes if _display_name(c) == class_name), None)
    if match is None:
        return []

    query = select(Student.name).where(
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
    # the roster any more, so no saved mark is hidden.
    scores_by_student = {
        row["student"]: {k: v for k, v in row.items() if k != "student"} for row in base.marks
    }
    names = list(
        dict.fromkeys([*await _class_roster(db, tenant_id, class_name), *scores_by_student])
    )

    averages = {name: _student_average(scores_by_student.get(name, {}), subjects) for name in names}
    ranked = sorted(names, key=lambda name: (-averages[name], name.lower()))
    rows = [
        RankedMarks(
            student=name,
            rank=position,
            average=_round_half_up(averages[name]),
            scores=scores_by_student.get(name, {}),
        )
        for position, name in enumerate(ranked, start=1)
    ]

    summary = PerformanceSummary(
        students=len(rows),
        average=_round_half_up(sum(r.average for r in rows) / len(rows)) if rows else 0,
        top=TopStudent(student=rows[0].student, average=rows[0].average) if rows else None,
    )

    # Search narrows the rows shown; ranks stay the student's rank in the class.
    if params.search:
        term = params.search.lower()
        rows = [r for r in rows if term in r.student.lower()]

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
