"""
Service layer for student attendance records.

Uses one bulk-sync operation on the attendance profile instead of separate
create/update/delete endpoints for each record.

Every operation is scoped by tenant_id. Records are only updated when their
IDs already belong to the current student's attendance profile.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.attendance import StudentAttendanceProfile, StudentAttendanceRecord
from app.db.schemas.attendance_profile import AttendanceProfileBulkUpdate

ModelT = TypeVar("ModelT", bound=StudentAttendanceRecord)


def _profile_query(
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Select[tuple[StudentAttendanceProfile]]:
    """
    Build the standard attendance-profile query.

    Relationships are eagerly loaded because AsyncSession does not support
    implicit lazy-loading through normal attribute access.
    """
    return (
        select(StudentAttendanceProfile)
        .where(
            StudentAttendanceProfile.student_id == student_id,
            StudentAttendanceProfile.tenant_id == tenant_id,
        )
        .options(selectinload(StudentAttendanceProfile.records))
    )


async def get_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StudentAttendanceProfile | None:
    """
    Return a student's attendance profile, including all records.

    The query is always tenant-scoped.
    """
    stmt = _profile_query(student_id=student_id, tenant_id=tenant_id)

    result = await db.execute(stmt)

    return result.scalar_one_or_none()


async def get_or_create_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StudentAttendanceProfile:
    """
    Atomically get or create an attendance profile.

    PostgreSQL ON CONFLICT prevents a check-then-insert race when two
    requests attempt to create the same student's profile concurrently.

    The profile is re-selected after the insert so all relationships are
    eagerly loaded before it is returned.
    """
    profile = await get_profile(db, student_id=student_id, tenant_id=tenant_id)

    if profile is not None:
        return profile

    insert_stmt = (
        pg_insert(StudentAttendanceProfile)
        .values(
            student_id=student_id,
            tenant_id=tenant_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "student_id",
            ]
        )
    )

    await db.execute(insert_stmt)
    await db.flush()

    profile = await get_profile(db, student_id=student_id, tenant_id=tenant_id)

    if profile is None:
        raise RuntimeError(
            f"Failed to get or create attendance profile for student_id={student_id}"
        )

    return profile


async def _sync_records(
    db: AsyncSession,
    *,
    existing: Iterable[ModelT],
    incoming: Sequence[BaseModel],
    model_cls: type[ModelT],
    profile_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    existing_by_id = {row.id: row for row in existing}
    seen_ids: set[uuid.UUID] = set()

    for item in incoming:
        item_id = getattr(item, "id", None)

        data = item.model_dump(
            exclude={"id", "profile_id", "tenant_id"},
            exclude_unset=True,
        )

        # New item
        if item_id is None:
            db.add(
                model_cls(
                    profile_id=profile_id,
                    tenant_id=tenant_id,
                    **data,
                )
            )
            continue

        # Existing item belonging to this profile
        existing_row = existing_by_id.get(item_id)

        if existing_row is not None:
            for field, value in data.items():
                setattr(existing_row, field, value)

            seen_ids.add(item_id)
            continue

        # ID supplied, but it isn't part of this profile.
        # Treat it as a new row if your frontend may send temporary/stale IDs.
        db.add(
            model_cls(
                profile_id=profile_id,
                tenant_id=tenant_id,
                **data,
            )
        )

    # Anything that existed before but wasn't sent back gets deleted.
    for existing_id, row in existing_by_id.items():
        if existing_id not in seen_ids:
            await db.delete(row)


async def sync_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
    data: AttendanceProfileBulkUpdate,
) -> StudentAttendanceProfile:
    """
    Synchronise a student's complete attendance profile.

    The scalar `notes` field is updated first, followed by `records` if
    present in the request.

    `records` set to None is left unchanged. `records` explicitly supplied
    as an empty list removes all existing records.
    """
    profile = await get_or_create_profile(db, student_id=student_id, tenant_id=tenant_id)

    scalar_fields = data.model_dump(
        exclude_unset=True,
        exclude={"records"},
    )

    for field, value in scalar_fields.items():
        setattr(profile, field, value)

    if data.records is not None:
        await _sync_records(
            db,
            existing=profile.records,
            incoming=data.records,
            model_cls=StudentAttendanceRecord,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    await db.commit()

    # Re-query instead of relying on lazy loading or refresh() to populate
    # relationship collections after the commit.
    refreshed_profile = await get_profile(db, student_id=student_id, tenant_id=tenant_id)

    if refreshed_profile is None:
        raise RuntimeError(
            f"Attendance profile disappeared after synchronisation for student_id={student_id}"
        )

    return refreshed_profile
