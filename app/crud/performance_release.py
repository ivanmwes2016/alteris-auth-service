"""
Publishing a class's term marks to parents.

Term marks (app.crud.performance) are entered over days or weeks, so parents see
none of a class's marks for a term until the school publishes them: Owner, Admins
and the Head Teacher (the edit_details roles). Publishing tells the parents of
every student with marks in that class/term/year by push, once; publishing again
changes nothing. Unpublishing hides the marks again. After publishing, corrections
show to parents as they're saved.

The class is named as the Performance page names it ("Primary 1 A": class and
stream), the same freeform string performance_records keeps.
"""

import math
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import BackgroundTasks
from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import parent_access
from app.crud.student_access import StudentCaller
from app.db.models.performance import PerformanceRecord, PerformanceRelease
from app.db.models.student import Student
from app.db.models.users import User
from app.db.schemas.parent_access import (
    ChildSubjectScore,
    ChildTermResults,
    ReleaseKey,
    ReleaseRead,
)
from app.services import parent_push


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def numeric(score: str) -> float | None:
    """A score that counts towards averages: a finite number, as the Performance page."""
    try:
        value = float(score.strip())
    except (ValueError, AttributeError):
        return None
    return value if math.isfinite(value) else None


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


# ---------------------------------------------------------------------------
# The school's side
# ---------------------------------------------------------------------------
def _where(tenant_id: uuid.UUID, key: ReleaseKey) -> tuple[ColumnElement[bool], ...]:
    return (
        PerformanceRelease.tenant_id == tenant_id,
        PerformanceRelease.class_name == key.class_name,
        PerformanceRelease.term_label == key.term,
        PerformanceRelease.year == key.year,
    )


async def get_release(db: AsyncSession, tenant_id: uuid.UUID, key: ReleaseKey) -> ReleaseRead:
    row = (
        await db.execute(
            select(PerformanceRelease, User.name, User.email)
            .outerjoin(User, User.id == PerformanceRelease.released_by)
            .where(*_where(tenant_id, key))
        )
    ).first()
    if row is None:
        return ReleaseRead(**key.model_dump(), released=False)
    release, name, email = row
    return ReleaseRead(
        **key.model_dump(),
        released=True,
        released_at=release.released_at,
        released_by_name=name or email,
    )


async def _students_with_marks(
    db: AsyncSession, tenant_id: uuid.UUID, key: ReleaseKey
) -> list[tuple[uuid.UUID, str]]:
    rows = await db.execute(
        select(Student.id, Student.name)
        .join(PerformanceRecord, PerformanceRecord.student_id == Student.id)
        .where(
            PerformanceRecord.tenant_id == tenant_id,
            PerformanceRecord.class_name == key.class_name,
            PerformanceRecord.term_label == key.term,
            PerformanceRecord.year == key.year,
        )
        .distinct()
    )
    return [(student_id, name) for student_id, name in rows.tuples()]


async def publish(
    db: AsyncSession,
    caller: StudentCaller,
    user: User,
    key: ReleaseKey,
    background: BackgroundTasks,
) -> ReleaseRead:
    inserted = await db.scalar(
        pg_insert(PerformanceRelease)
        .values(
            id=uuid.uuid4(),
            tenant_id=caller.tenant_id,
            class_name=key.class_name,
            term_label=key.term,
            year=key.year,
            released_at=datetime.now(UTC),
            released_by=user.id,
        )
        .on_conflict_do_nothing(constraint="uq_performance_release")
        .returning(PerformanceRelease.id)
    )
    await db.commit()
    result = await get_release(db, caller.tenant_id, key)
    if inserted is None:
        return result  # already published: parents were told then

    students = await _students_with_marks(db, caller.tenant_id, key)
    recipients = await parent_access.recipients(db, [s for s, _ in students], "results")
    notices = [
        parent_push.results_notice(
            student_id=student_id, student_name=name, term=key.term, year=key.year
        )
        for student_id, name in students
        if recipients.get(student_id)
    ]
    background.add_task(parent_push.notify_parents, notices)
    result.parents_notified = len({u for users in recipients.values() for u in users})
    return result


async def unpublish(db: AsyncSession, caller: StudentCaller, key: ReleaseKey) -> ReleaseRead:
    await db.execute(delete(PerformanceRelease).where(*_where(caller.tenant_id, key)))
    await db.commit()
    return ReleaseRead(**key.model_dump(), released=False)


# ---------------------------------------------------------------------------
# The parent's side
# ---------------------------------------------------------------------------
async def child_results(db: AsyncSession, student: Student) -> list[ChildTermResults]:
    """The child's marks for every published class/term, newest first."""
    rows = (
        await db.execute(
            select(PerformanceRecord, PerformanceRelease.released_at)
            .join(
                PerformanceRelease,
                (PerformanceRelease.tenant_id == PerformanceRecord.tenant_id)
                & (PerformanceRelease.class_name == PerformanceRecord.class_name)
                & (PerformanceRelease.term_label == PerformanceRecord.term_label)
                & (PerformanceRelease.year == PerformanceRecord.year),
            )
            .where(
                PerformanceRecord.tenant_id == student.tenant_id,
                PerformanceRecord.student_id == student.id,
            )
        )
    ).all()
    if not rows:
        return []

    terms: dict[tuple[str, str, int], list[PerformanceRecord]] = defaultdict(list)
    released: dict[tuple[str, str, int], datetime] = {}
    for record, released_at in rows:
        term_key = (record.class_name, record.term_label, record.year)
        terms[term_key].append(record)
        released[term_key] = released_at

    # Class averages per subject, over everyone's numeric scores in those terms.
    class_scores: dict[tuple[str, str, int, str], list[float]] = defaultdict(list)
    for record in await db.scalars(
        select(PerformanceRecord).where(
            PerformanceRecord.tenant_id == student.tenant_id,
            PerformanceRecord.class_name.in_({k[0] for k in terms}),
            PerformanceRecord.term_label.in_({k[1] for k in terms}),
            PerformanceRecord.year.in_({k[2] for k in terms}),
        )
    ):
        value = numeric(record.score)
        if value is not None:
            class_scores[
                (record.class_name, record.term_label, record.year, record.subject)
            ].append(value)

    results: list[ChildTermResults] = []
    for term_key, records in terms.items():
        class_name, term, year = term_key
        own = [v for v in (numeric(r.score) for r in records) if v is not None]
        results.append(
            ChildTermResults(
                term=term,
                year=year,
                class_name=class_name,
                released_at=released[term_key],
                average=mean(own),
                scores=[
                    ChildSubjectScore(
                        subject=r.subject,
                        score=r.score,
                        class_average=mean(class_scores.get((*term_key, r.subject), [])),
                    )
                    for r in sorted(records, key=lambda r: r.subject)
                ],
            )
        )
    results.sort(key=lambda r: r.released_at, reverse=True)
    return results
