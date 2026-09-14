"""
Service layer for academic performance (marks entry).

Marks are stored one row per (student, subject) for a given class/term/year
and fully replaced on every PATCH, matching the frontend's editedMarks —
the whole roster is resent on every save. A blank/missing score for a
(student, subject) pair means "not persisted": any existing row for it gets
deleted rather than stored as an empty string.

`classAverage` and trend averages only count entered numeric scores — a
student with no mark yet for a subject isn't counted as a zero.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.performance import PerformanceRecord
from app.db.schemas.performance import PerformanceRead, PerformanceUpdate, TrendPoint


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
    subjects_seen: set[str] = set()

    for record in records:
        row = marks_by_student.setdefault(record.student_name, {"student": record.student_name})
        row[record.subject] = record.score
        subjects_seen.add(record.subject)

        score = _safe_float(record.score)
        if score is not None:
            scores_by_subject[record.subject].append(score)

    class_average = {
        subject: round(sum(scores) / len(scores), 1)
        for subject, scores in scores_by_subject.items()
        if scores
    }

    trends = await _get_trends(db, tenant_id=tenant_id, class_name=class_name)

    return PerformanceRead(
        subjects=sorted(subjects_seen),
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
        if key not in seen_keys:
            await db.delete(existing_row)

    await db.commit()

    return await get_performance(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term_label, year=year
    )
